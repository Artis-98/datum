"""The drawing picture for the website: a part worth drawing, laid out well.

A bearing housing, because it has what a drawing is for: a hole pattern
on a plate, a boss with a stepped bore that only a section shows
properly, and material to hatch. Front view, plan, section A-A and an
isometric, dimensioned, on an A3 sheet with the views filling the space
the title block leaves.

    python tests/shot_site_drawing.py [output.png]
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-shots"

from PySide6 import QtCore, QtWidgets                              # noqa: E402

from datum.core import fileformat                                  # noqa: E402
from datum.core.document import Document                           # noqa: E402
from datum.core.drawing import (                                   # noqa: E402
    Annotation, BASE, CENTRE_MARK, DIAMETER, LINEAR, PROJECTED, SECTION,
    View, WITH_HIDDEN,
)
from datum.core.features import PrimitiveFeature                   # noqa: E402
from datum.ui import icons                                         # noqa: E402
from datum.ui.main_window import MainWindow                        # noqa: E402
from datum.ui.theme import stylesheet                              # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "site", "img", "drawing.png")

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
app.setWindowIcon(icons.app_icon())
win = MainWindow()
win.resize(1600, 958)
win.show()

# the housing, in millimetres: plate, boss, stepped bore, four bolt holes
PLATE = (160.0, 90.0, 16.0)
BOSS_R, BOSS_TOP = 40.0, 72.0
BORE_R, COUNTER_R, COUNTER_DEPTH = 23.5, 26.0, 10.0
HOLES = [(18.0, 18.0), (142.0, 18.0), (18.0, 72.0), (142.0, 72.0)]
HOLE_R, SPOT_R = 6.5, 11.0
CX, CY = PLATE[0] / 2.0, PLATE[1] / 2.0


def settle(ms=500):
    timer = QtCore.QElapsedTimer()
    timer.start()
    while timer.elapsed() < ms:
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 20)


def primitive(kind, a, b, c, origin, operation):
    feature = PrimitiveFeature()
    feature.kind = kind
    feature.a, feature.b, feature.c = "%g" % a, "%g" % b, "%g" % c
    feature.origin = tuple("%g" % v for v in origin)
    feature.operation = operation
    return feature


def housing(path):
    part = Document()
    part.material = "Aluminium 6061"
    part.properties.update({"Title": "BEARING HOUSING",
                            "PartNumber": "BH-100"})
    part.add_feature(primitive("box", *PLATE, (0, 0, 0), "new"))
    part.add_feature(primitive("cylinder", BOSS_R, BOSS_TOP - PLATE[2], 0,
                               (CX, CY, PLATE[2]), "join"))
    part.add_feature(primitive("cylinder", BORE_R, BOSS_TOP + 10, 0,
                               (CX, CY, -5), "cut"))
    part.add_feature(primitive("cylinder", COUNTER_R, COUNTER_DEPTH + 5, 0,
                               (CX, CY, BOSS_TOP - COUNTER_DEPTH), "cut"))
    for x, y in HOLES:
        part.add_feature(primitive("cylinder", HOLE_R, PLATE[2] + 10, 0,
                                   (x, y, -5), "cut"))
        # a spot face round each, where the bolt head sits
        part.add_feature(primitive("cylinder", SPOT_R, 5, 0,
                                   (x, y, PLATE[2] - 2), "cut"))
    part.rebuild()
    part.save(path)
    return path


def go():
    work = tempfile.mkdtemp(prefix="datum_site_drawing_")
    part = housing(os.path.join(work, "Bearing Housing.pdat"))

    win.new_drawing(prompt=False)
    settle(400)
    doc = win.drawing
    doc.path = os.path.join(work, "Bearing Housing.ddat")
    doc.properties.update({"Title": "BEARING HOUSING", "Author": "aa",
                           "Company": "IITEG", "Revision": "B",
                           "PartNumber": "BH-100"})
    sheet = doc.active()
    x0, y0, x1, y1 = doc.borders[sheet.border].frame(*sheet.extent())[:4]
    block = doc.title_blocks[sheet.title_block]

    # Laid out by hand on the sheet, all at 1:1: the front view top left
    # with the plan under it, section A-A beside the front, cut straight
    # down through the bore, and the isometric at 1:2 under the section,
    # clear of the title block. Two columns, each with room for the
    # dimensions either side of it, spread evenly across the frame.
    left_w = 26.0 + PLATE[0] + 28.0         # front and plan, with their dims
    right_w = PLATE[1] + 26.0               # the section, with its depth dim
    gap = ((x1 - x0) - left_w - right_w) / 3.0
    front_x = x0 + gap + 26.0 + PLATE[0] / 2.0
    section_x = x0 + gap + left_w + gap + PLATE[1] / 2.0
    height = y1 - y0
    front_y = y0 + height * 0.76
    plan_y = y0 + height * 0.29
    iso_x = section_x
    iso_y = y0 + block.height + (front_y - BOSS_TOP / 2.0 - y0
                                 - block.height) * 0.5

    ref = fileformat.ComponentRef(path=fileformat.relative_path(part, work),
                                  name=os.path.basename(part))
    # the two principal views go unlabelled, as a drawing leaves them
    front = View(kind=BASE, orientation="front", scale=1.0, x=front_x,
                 y=front_y, display=WITH_HIDDEN, name="Front",
                 label_visible=False)
    front.ref = ref
    doc.add_view(sheet, front)
    plan = doc.add_view(sheet, View(kind=PROJECTED, parent=front.id,
                                    name="Plan", x=front_x, y=plan_y,
                                    label_visible=False))
    # The isometric is a view of its own, not a projection of the front:
    # a projected one off a corner is seen from that corner, and this one
    # sits below and to the right, which would be the housing from below.
    iso = View(kind=BASE, orientation="iso", name="Iso", display="visible",
               scale=0.5, x=iso_x, y=iso_y)
    iso.ref = ref
    doc.add_view(sheet, iso)
    win.drawing_ui.rebuild(keep_view=False)
    settle(500)

    # the section is cut on the front view, down through the bore, so it
    # stands upright beside it; the line goes where the view drew the bore
    fb = front.projection.box
    pb = plan.projection.box
    mid_x = (fb[0] + fb[2]) / 2.0
    section = doc.add_view(sheet, View(
        kind=SECTION, parent=front.id, letter="A",
        cut=[mid_x, fb[3] + 6.0, mid_x, fb[1] - 6.0],
        x=section_x, y=front_y, display="visible"))
    for view in (section, iso):
        view.scale_visible = True
    win.drawing_ui.rebuild()
    settle(600)

    def dim(view, kind, a, b, offset, prefix=""):
        doc.add_annotation(sheet, Annotation(kind=kind, view=view.id,
                                             points=[list(a), list(b)],
                                             offset=list(offset),
                                             prefix=prefix))

    # front: the height from the base to the top of the boss on the left,
    # the plate on the right; the width goes on the plan, clear of the
    # section line
    boss_left = fb[0] + CX - BOSS_R
    dim(front, LINEAR, (boss_left, fb[1]), (boss_left, fb[3]),
        (fb[0] - boss_left - 14.0, 0.0))
    dim(front, LINEAR, (fb[2], fb[1]), (fb[2], fb[1] + PLATE[2]),
        (10.0, 0.0))

    # plan: the hole pattern, a hole, the boss, and their centres
    def on_plan(x, y):
        return (pb[0] + x, pb[1] + y)

    dim(plan, LINEAR, (pb[0], pb[1]), (pb[2], pb[1]), (0.0, -12.0))
    dim(plan, LINEAR, on_plan(*HOLES[2]), on_plan(*HOLES[3]),
        (0.0, PLATE[1] - HOLES[2][1] + 10.0))
    dim(plan, LINEAR, on_plan(*HOLES[1]), on_plan(*HOLES[3]),
        (PLATE[0] - HOLES[1][0] + 10.0, 0.0))
    hx, hy = on_plan(*HOLES[2])
    dim(plan, DIAMETER, (hx, hy), (hx - HOLE_R * 0.7071, hy + HOLE_R * 0.7071),
        (0.0, 0.0))
    bx, by = on_plan(CX, CY)
    dim(plan, DIAMETER, (bx, by), (bx + BOSS_R * 0.7071, by + BOSS_R * 0.7071),
        (0.0, 0.0))
    for x, y in HOLES + [(CX, CY)]:
        px, py = on_plan(x, y)
        doc.add_annotation(sheet, Annotation(kind=CENTRE_MARK, view=plan.id,
                                             points=[[px, py]], value=5.0))

    # section: the bore, the counterbore and its depth
    sb = section.projection.box
    s_mid = (sb[0] + sb[2]) / 2.0
    bore_z = sb[1] + (BOSS_TOP - COUNTER_DEPTH) * 0.5
    dim(section, LINEAR, (s_mid - BORE_R, bore_z), (s_mid + BORE_R, bore_z),
        (0.0, 0.0), prefix="⌀")
    dim(section, LINEAR, (s_mid - COUNTER_R, sb[3]),
        (s_mid + COUNTER_R, sb[3]), (0.0, 10.0), prefix="⌀")
    dim(section, LINEAR, (s_mid + COUNTER_R, sb[3] - COUNTER_DEPTH),
        (s_mid + COUNTER_R, sb[3]), (BOSS_R - COUNTER_R + 12.0, 0.0))

    win.drawing_ui.rebuild()
    settle(500)
    win.sheet_canvas.fit()
    win.drawing_ui.browser.expandAll()
    win.status_message.setText("Bearing Housing.ddat: 4 views, 13 "
                               "annotations, up to date")
    settle(600)
    picture = win.grab()
    picture.save(OUT)
    print("saved", OUT, picture.width(), "x", picture.height())
    print("views", [(v.name or v.label, round(v.x), round(v.y),
                     v.projection.box if v.projection else v.error)
                    for v in sheet.views])
    app.quit()


QtCore.QTimer.singleShot(1600, go)
sys.exit(app.exec())
