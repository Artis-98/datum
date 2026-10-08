"""Horizontal and Vertical between two points, not only on a line.

Inventor levels two points with the same command it uses on a line: a hole
centre with a corner, two circle centres with each other.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtWidgets                                      # noqa: E402

from datum.core.sketch import Sketch                               # noqa: E402
from datum.ui.main_window import MainWindow                        # noqa: E402
from datum.ui.theme import stylesheet                              # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


print("the solver")
s = Sketch()
a = s.add_circle((0.0, 0.0), 5.0)
b = s.add_circle((40.0, 13.0), 5.0)
ca, cb = s.entities[a].points[0], s.entities[b].points[0]
s.add_constraint("horizontal", points=[ca, cb])
s.solve()
check("two centres end up level",
      abs(s.points[ca].y - s.points[cb].y) < 1e-6,
      (s.points[ca].y, s.points[cb].y))
check("and stay apart", abs(s.points[ca].x - s.points[cb].x) > 30.0)

c = s.add_point(7.0, -30.0)
s.add_constraint("vertical", points=[ca, c])
s.solve()
check("a point drops in line under a centre",
      abs(s.points[ca].x - s.points[c].x) < 1e-6,
      (s.points[ca].x, s.points[c].x))

saved = Sketch.from_dict(s.to_dict())
check("it survives a save", any(
    k.kind == "vertical" and len(k.points) == 2 and not k.entities
    for k in saved.constraints.values()))

line = s.add_line((0.0, 50.0), (30.0, 57.0))
s.add_constraint("horizontal", entities=[line])
s.solve()
p, q = s.entities[line].points
check("on a line it still works as before",
      abs(s.points[p].y - s.points[q].y) < 1e-6)

print()
print("the command")
app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
win = MainWindow()
win.resize(1400, 900)
win.show()
app.processEvents()
ed = win.editor
win.new_document(prompt=False)
win.start_sketch_on_plane("XY")
app.processEvents()
sk = ed.sketch
one = sk.add_circle((-30.0, 10.0), 4.0)
two = sk.add_circle((25.0, 22.0), 4.0)
p1, p2 = sk.entities[one].points[0], sk.entities[two].points[0]
sk.solve()

ed.clear_selection()
ed.start_constraint("horizontal")
check("armed with nothing picked, it waits",
      ed._pending_constraint == "horizontal")
ed.selected_points = [p1]
ed._check_pending_constraint()
check("one point is not enough", ed._pending_constraint == "horizontal")
ed.selected_points = [p1, p2]
ed._check_pending_constraint()
check("the second point fires it, and it stays armed for the next",
      ed._pending_constraint == "horizontal" and not ed.selected_points)
ed.escape()
check("and the centres are level",
      abs(sk.points[p1].y - sk.points[p2].y) < 1e-6,
      (sk.points[p1].y, sk.points[p2].y))

ed.selected_points = [p1, p2]
ed.selected_entities = []
ed.apply_constraint("vertical")
check("Vertical on two picked points stacks them",
      abs(sk.points[p1].x - sk.points[p2].x) < 1e-6
      and not sk.conflicting, (sk.points[p1].x, sk.points[p2].x))

print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
