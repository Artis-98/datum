"""Drawing views worked out on every core, and drawn exactly as before.

Every plain view of a sheet is the model seen from one direction, and
none of them depends on another's lines, so they are handed to the
workers together. The test is that nobody could tell: the same lines, to
the last digit, as the same sheet drawn in one process.
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WORK = tempfile.mkdtemp(prefix="datum_pdraw_")
os.environ["DATUM_CONFIG_DIR"] = os.path.join(WORK, "cfg")
os.environ.pop("DATUM_NO_WORKERS", None)
os.environ["DATUM_WORKERS"] = "4"

from datum.core import fileformat, views, workers                # noqa: E402
from datum.core.drawing import (BASE, DETAIL, PROJECTED,         # noqa: E402
                                WITH_HIDDEN, DrawingDocument, View)


def reference(path, base_dir):
    return fileformat.ComponentRef(
        path=fileformat.relative_path(path, base_dir),
        name=os.path.basename(path),
        label=os.path.splitext(os.path.basename(path))[0])

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(ROOT, "examples", "excavator", "Excavator.adat")
FAILS = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def sheet_of_views():
    doc = DrawingDocument()
    doc.path = os.path.join(WORK, "Excavator.ddat")
    sheet = doc.add_sheet("A2")
    base = View(kind=BASE, orientation="front", scale=0.05, x=150, y=250,
                display=WITH_HIDDEN, name="Front")
    base.ref = reference(MODEL, WORK)
    doc.add_view(sheet, base)
    doc.add_view(sheet, View(kind=PROJECTED, parent=base.id, name="Top",
                             x=150, y=100))
    doc.add_view(sheet, View(kind=PROJECTED, parent=base.id, name="Side",
                             x=350, y=250))
    doc.add_view(sheet, View(kind=PROJECTED, parent=base.id, name="Iso",
                             x=350, y=100, display="visible"))
    doc.add_view(sheet, View(kind=DETAIL, parent=base.id, letter="A",
                             scale=0.2, centre=[0.0, 0.0], radius=12.0,
                             x=500, y=250, name="Detail"))
    return doc, sheet


def lines_of(sheet):
    return {v.name: [(line.kind, [tuple(round(c, 9) for c in p)
                                  for p in line.points])
                     for line in v.projection.lines] if v.projection else None
            for v in sheet.views}


print("the same sheet, drawn both ways")

workers.ENABLED = False
alone, alone_sheet = sheet_of_views()
started = time.perf_counter()
report_alone = views.Generator().rebuild(alone, force=True)
serial = time.perf_counter() - started

workers.ENABLED = True
helpers = workers.pool()
helpers.start()
helpers.wait_ready(timeout=90)
shared, shared_sheet = sheet_of_views()
generator = views.Generator()
handed = []
real = generator._hand_out


def spy(*args, **kwargs):
    out = real(*args, **kwargs)
    handed.append(len(out))
    return out


generator._hand_out = spy
started = time.perf_counter()
report_shared = generator.rebuild(shared, force=True)
parallel = time.perf_counter() - started

check("the plain views went to the workers", handed and handed[0] == 4,
      handed)
check("every view was drawn", report_shared.ok and all(
    v.projection is not None for v in shared_sheet.views),
    report_shared.message)
check("and they are the lines drawn in one process, exactly",
      lines_of(shared_sheet) == lines_of(alone_sheet))
print("  (one process %.2f s, with workers %.2f s)" % (serial, parallel))

helpers.shutdown()


print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
