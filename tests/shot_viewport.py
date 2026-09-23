"""Render a model through the real Viewport widget and dump a PNG."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


from PySide6 import QtCore, QtWidgets  # noqa: E402

from datum.core.document import Document  # noqa: E402
from datum.core.features import (  # noqa: E402
    ExtrudeFeature, FilletFeature, HoleFeature, SketchFeature,
)
from datum.core.naming import RefSet  # noqa: E402
from datum.core.sketch import STANDARD_PLANES, Sketch  # noqa: E402
from datum.core import kernel  # noqa: E402
from datum.ui.theme import C, stylesheet  # noqa: E402
from datum.ui.viewport import Viewport  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "viewport.png"


def build_part():
    doc = Document()
    doc.params.add("w", "80")
    doc.params.add("d", "50")
    doc.params.add("t", "10")

    sk = SketchFeature()
    sk.sketch = Sketch(STANDARD_PLANES["XY"], "Outline")
    sk.sketch.add_rectangle((0, 0), (80, 50))
    doc.add_feature(sk)

    ex = ExtrudeFeature()
    ex.sketch_id = sk.id
    ex.distance = "t"
    doc.add_feature(ex)
    doc.rebuild()

    verticals = [e for e in kernel.edges(doc.shape)
                 if abs(kernel.edge_length(e) - 10) < 0.01]
    fl = FilletFeature()
    fl.radius = "8"
    fl.refs = RefSet()
    fl.refs.capture_from(doc.shape, "edge", verticals)
    doc.add_feature(fl)

    hs = SketchFeature()
    hs.sketch = Sketch(STANDARD_PLANES["XY"], "Holes")
    for p in [(12, 12), (68, 12), (12, 38), (68, 38), (40, 25)]:
        hs.sketch.add_circle(p, 3)
    doc.add_feature(hs)

    h = HoleFeature()
    h.sketch_id = hs.id
    h.diameter = "6"
    h.through = True
    h.flip = True
    doc.add_feature(h)

    print(doc.rebuild().message)
    return doc


app = QtWidgets.QApplication(sys.argv)
app.setStyleSheet(stylesheet())

win = QtWidgets.QWidget()
win.resize(1100, 720)
lay = QtWidgets.QVBoxLayout(win)
lay.setContentsMargins(0, 0, 0, 0)
vp = Viewport()
lay.addWidget(vp)
win.show()

doc = build_part()


def go():
    vp.set_shape(doc.shape, keep_camera=False)
    vp.set_view("iso")
    ok = vp.grab_image(OUT)
    print("dumped:", ok, OUT)
    app.quit()


QtCore.QTimer.singleShot(1500, go)
sys.exit(app.exec())
