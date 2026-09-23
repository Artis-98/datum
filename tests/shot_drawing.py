"""Screenshot the drawing workspace: the sheet, the tree, the ribbon."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-shots"

from PySide6 import QtCore, QtGui, QtWidgets                       # noqa: E402

from datum.core import fileformat, kernel                          # noqa: E402
from datum.core.document import Document                           # noqa: E402
from datum.core.drawing import (                                   # noqa: E402
    Annotation, BASE, DETAIL, DrawingDocument, LINEAR, PROJECTED, SECTION,
    View, WITH_HIDDEN, three_view_layout,
)
from datum.ui import icons                                         # noqa: E402
from datum.ui.drawing_ui import TemplateDialog                     # noqa: E402
from datum.ui.main_window import MainWindow                        # noqa: E402
from datum.ui.theme import stylesheet                              # noqa: E402

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "examples", "plywood-tote")
OUT = sys.argv[1] if len(sys.argv) > 1 else "."
os.makedirs(OUT, exist_ok=True)

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
app.setWindowIcon(icons.app_icon())
win = MainWindow()
win.resize(1600, 980)
win.show()

_n = [0]


def settle(ms=500):
    timer = QtCore.QElapsedTimer()
    timer.start()
    while timer.elapsed() < ms:
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 20)


def shot(name, extra=None):
    settle(250)
    _n[0] += 1
    chrome = win.grab()
    if extra is not None and extra.isVisible():
        p = QtGui.QPainter(chrome)
        p.drawPixmap(win.mapFromGlobal(extra.mapToGlobal(QtCore.QPoint(0, 0))),
                     extra.grab())
        p.end()
    chrome.save(os.path.join(OUT, "%02d_%s.png" % (_n[0], name)))
    print("shot:", name)


def go():
    work = tempfile.mkdtemp(prefix="datum_shot_drawing_")
    part = os.path.join(SRC, "Base.pdat")

    dialog = TemplateDialog(win, win.project_folder())
    dialog.move(win.mapToGlobal(QtCore.QPoint(520, 240)))
    dialog.show()
    settle(400)
    shot("new_from_template", dialog)
    dialog.close()

    win.new_drawing(prompt=False)
    settle(400)
    doc = win.drawing
    doc.path = os.path.join(work, "Base.ddat")
    doc.properties.update({"Title": "TOTE BASE", "Author": "aa",
                           "Company": "IITEG", "Revision": "A"})
    sheet = doc.active()

    model = Document.load(part)
    model.rebuild()
    box = kernel.bounding_box(model.shape)
    span = (box[3] - box[0], box[4] - box[1], box[5] - box[2])

    frame = doc.borders[sheet.border].frame(*sheet.extent())
    block = doc.title_blocks[sheet.title_block]
    plan = three_view_layout(frame, span, reserve=(block.width, block.height))

    base = View(kind=BASE, orientation="top", scale=plan["scale"],
                x=plan["base"][0], y=plan["base"][1], display=WITH_HIDDEN,
                name="Plan")
    base.ref = fileformat.ComponentRef(
        path=fileformat.relative_path(part, work),
        name=os.path.basename(part))
    doc.add_view(sheet, base)
    doc.add_view(sheet, View(kind=PROJECTED, parent=base.id, name="Front",
                             x=plan["below"][0], y=plan["below"][1]))
    doc.add_view(sheet, View(kind=PROJECTED, parent=base.id, name="End",
                             x=plan["side"][0], y=plan["side"][1]))
    doc.add_view(sheet, View(kind=PROJECTED, parent=base.id, name="Iso",
                             display="visible", scale=plan["iso_scale"],
                             x=plan["iso"][0], y=plan["iso"][1]))
    for view in sheet.views:
        view.scale_visible = True

    win.drawing_ui.rebuild(keep_view=False)
    settle(500)

    projection = base.projection
    if projection is not None:
        b = projection.box
        doc.add_annotation(sheet, Annotation(
            kind=LINEAR, view=base.id,
            points=[[b[0], b[1]], [b[2], b[1]]], offset=[0.0, -15.0]))
        doc.add_annotation(sheet, Annotation(
            kind=LINEAR, view=base.id,
            points=[[b[2], b[1]], [b[2], b[3]]], offset=[17.0, 0.0]))
    win.drawing_ui.rebuild()
    settle(400)
    win.drawing_ui.browser.expandAll()
    shot("drawing_workspace")

    # a view selected, with the tree following it
    win.sheet_canvas.select([base.id])
    settle(300)
    shot("view_selected")

    # zoomed in on the title block, where the property fields resolve
    canvas = win.sheet_canvas
    layout = canvas.layout()
    width, height = sheet.extent()
    px, py = layout.to_device(width - 100.0, 30.0)
    canvas.zoom(3.2, QtCore.QPoint(int(px), int(py)))
    settle(400)
    shot("title_block")
    canvas.fit()
    settle(300)

    print("DONE  -  %s" % win.status_build.text())
    app.quit()


QtCore.QTimer.singleShot(1600, go)
sys.exit(app.exec())
