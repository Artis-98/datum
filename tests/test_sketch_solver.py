"""Constraint solver checks - run with: python tests/test_sketch_solver.py"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


from datum.core.params import ParameterTable, evaluate  # noqa: E402
from datum.core.sketch import Sketch  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s  %s" % (name, extra))
        FAILS.append(name)


def near(a, b, tol=1e-5):
    return abs(a - b) < tol


print("expressions")
check("arithmetic", near(evaluate("2 + 3 * 4"), 14))
check("units mm", near(evaluate("2 in"), 50.8))
check("trig in degrees", near(evaluate("sin(30)"), 0.5))
check("scope lookup", near(evaluate("w / 2", {"w": 50}), 25))

t = ParameterTable()
t.add("width", "100")
t.add("wall", "2.5")
t.add("inner", "width - 2 * wall")
check("dependent param", near(t["inner"].value, 95))
t.set_expression("width", "120")
check("param propagation", near(t["inner"].value, 115))
t.add("loopa", "loopb")
t.add("loopb", "loopa")
check("cycle detected", "circular" in t["loopa"].error)
t.rename("wall", "thickness")
check("rename rewrites refs", t["inner"].expression == "width - 2 * thickness")
check("rename keeps value", near(t["inner"].value, 115))

print("rectangle: dimension drives geometry")
s = Sketch()
ids = s.add_rectangle((0, 0), (37.3, 21.9))
corner = s.entities[ids[0]].points
bottom_left, bottom_right = corner[0], corner[1]
left = s.entities[ids[3]].points
s.points[bottom_left].fixed = True
s.add_constraint("distance_x", points=[bottom_left, bottom_right], value=80.0)
s.add_constraint("distance_y", points=[left[1], left[0]], value=40.0)
ok = s.solve()
p_bl = s.points[bottom_left]
p_br = s.points[bottom_right]
check("solve converged", ok, s.solve_message)
check("width driven to 80", near(p_br.x - p_bl.x, 80.0, 1e-4),
      "got %.6f" % (p_br.x - p_bl.x))
check("corner stayed at origin", near(p_bl.x, 0) and near(p_bl.y, 0))
check("fully constrained", s.dof == 0, "dof=%d" % s.dof)
xs = sorted(p.x for p in s.points.values())
ys = sorted(p.y for p in s.points.values())
check("height driven to 40", near(ys[-1] - ys[0], 40.0, 1e-4),
      "got %.6f" % (ys[-1] - ys[0]))

print("expression-driven dimension")
tbl = ParameterTable()
tbl.add("plate_w", "64")
s2 = Sketch()
r = s2.add_rectangle((0, 0), (10, 10))
pts = s2.entities[r[0]].points
s2.points[pts[0]].fixed = True
c = s2.add_constraint("distance_x", points=[pts[0], pts[1]], expression="plate_w / 2")
s2.solve(tbl.scope())
check("expression dimension", near(s2.points[pts[1]].x - s2.points[pts[0]].x, 32, 1e-4),
      "got %.6f" % (s2.points[pts[1]].x - s2.points[pts[0]].x))
tbl.set_expression("plate_w", "100")
s2.solve(tbl.scope())
check("re-solve after param change",
      near(s2.points[pts[1]].x - s2.points[pts[0]].x, 50, 1e-4),
      "got %.6f" % (s2.points[pts[1]].x - s2.points[pts[0]].x))

print("circle radius + concentric")
s3 = Sketch()
c1 = s3.add_circle((0, 0), 5)
c2 = s3.add_circle((1.2, -0.7), 12)
s3.points[s3.entities[c1].points[0]].fixed = True
s3.add_constraint("concentric", entities=[c1, c2])
s3.add_constraint("radius", entities=[c1], value=8.0)
s3.add_constraint("diameter", entities=[c2], value=40.0)
ok3 = s3.solve()
check("concentric solved", ok3, s3.solve_message)
check("radius = 8", near(s3.entities[c1].radius, 8.0, 1e-5))
check("diameter 40 -> r 20", near(s3.entities[c2].radius, 20.0, 1e-5))
check("centres coincide",
      near(s3.points[s3.entities[c2].points[0]].x, 0, 1e-5))

print("tangent line to circle")
s4 = Sketch()
circ = s4.add_circle((0, 0), 10)
s4.points[s4.entities[circ].points[0]].fixed = True
s4.add_constraint("radius", entities=[circ], value=10.0)
ln = s4.add_line((-30, 14), (30, 14), weld=False)
lp = s4.entities[ln].points
s4.add_constraint("horizontal", entities=[ln])
s4.add_constraint("tangent", entities=[ln, circ])
ok4 = s4.solve()
check("tangent solved", ok4, s4.solve_message)
check("line sits at y = +/-10", near(abs(s4.points[lp[0]].y), 10.0, 1e-4),
      "got %.6f" % s4.points[lp[0]].y)

print("perpendicular + parallel")
s5 = Sketch()
a = s5.add_line((0, 0), (50, 3), weld=False)
b = s5.add_line((50, 3), (47, 40), weld=False)
ap, bp = s5.entities[a].points, s5.entities[b].points
s5.add_constraint("coincident", points=[ap[1], bp[0]])
s5.points[ap[0]].fixed = True
s5.add_constraint("horizontal", entities=[a])
s5.add_constraint("perpendicular", entities=[a, b])
ok5 = s5.solve()
va = (s5.points[ap[1]].x - s5.points[ap[0]].x,
      s5.points[ap[1]].y - s5.points[ap[0]].y)
vb = (s5.points[bp[1]].x - s5.points[bp[0]].x,
      s5.points[bp[1]].y - s5.points[bp[0]].y)
dot = va[0] * vb[0] + va[1] * vb[1]
check("perpendicular solved", ok5, s5.solve_message)
check("dot product ~ 0", near(dot, 0, 1e-3), "got %.6f" % dot)

print("under-constrained reporting")
s6 = Sketch()
s6.add_line((0, 0), (10, 0), weld=False)
s6.solve()
check("reports DOF", s6.dof == 4, "dof=%d" % s6.dof)

print("drag anchor")
s7 = Sketch()
lid = s7.add_line((0, 0), (10, 0), weld=False)
p = s7.entities[lid].points
s7.points[p[0]].fixed = True
s7.add_constraint("distance", points=[p[0], p[1]], value=10.0)
s7.solve(anchors={p[1]: (0.0, 50.0)})
d = math.hypot(s7.points[p[1]].x, s7.points[p[1]].y)
check("length held while dragging", near(d, 10.0, 1e-4), "got %.6f" % d)
check("dragged toward cursor", s7.points[p[1]].y > 5)

print("serialisation round trip")
blob = s.to_dict()
s8 = Sketch.from_dict(blob)
check("roundtrip entities", len(s8.entities) == len(s.entities))
check("roundtrip solves", s8.solve())

print()
if FAILS:
    print("%d FAILURES: %s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("all sketch/solver tests passed")
