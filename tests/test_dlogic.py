"""dLogic, the rest of it: code features, reach, triggers, trust.

test_rules.py holds the first promises: a rule moves the model and does
nothing until it is allowed to. This holds the ones that came after, and
the one that matters most is the same one again, for code features: a
part that builds itself from a script must not run that script just by
being opened.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WORK = tempfile.mkdtemp(prefix="datum_dlogic_")
os.environ["DATUM_CONFIG_DIR"] = os.path.join(WORK, "cfg")

from datum.core import geometry, prefs, rules                 # noqa: E402
from datum.core.assembly import AssemblyDocument              # noqa: E402
from datum.core.document import Document                      # noqa: E402
from datum.core.features import (CodeFeature, NEW_BODY,       # noqa: E402
                                 SketchFeature)

FAILS = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def source(*lines):
    return "\n".join(lines)


def code_part(src, trusted=True):
    doc = Document()
    doc.params.add("n", "4")
    doc.params.add("size", "20")
    feature = CodeFeature()
    feature.name = "Posts"
    feature.source = src
    feature.operation = NEW_BODY
    doc.add_feature(feature)
    doc.rules.trusted = trusted
    doc.rebuild()
    return doc, feature


print("the geometry toolkit")

post = geometry.box(10, 10, 100)
check("a box has the volume it should", abs(post.volume - 10000) < 1e-6)
row = post.repeat(5, x=30)
check("repeat makes separate copies", abs(row.volume - 50000) < 1e-3)
check("  spaced where they were asked", abs(row.size[0] - 130) < 1e-6,
      row.size)
check("moving gives a copy and leaves the original",
      abs(post.move(50).bbox[0] - 50) < 1e-3 and abs(post.bbox[0]) < 1e-3)
check("a boolean is an operator",
      abs((geometry.box(10, 10, 10) - geometry.box(5, 10, 10)).volume - 500)
      < 1e-6)
bar = geometry.rod((0, 0, 0), (0, 300, 400), 5)
check("a rod runs between its two points",
      abs(bar.size[1] - 310) < 12 and abs(bar.size[2] - 400) < 12, bar.size)
flat = geometry.beam((0, 0, 0), (1000, 0, 0), 40, 200)
check("a beam stands its height up", abs(flat.size[2] - 200) < 1e-6 and
      abs(flat.size[1] - 40) < 1e-6, flat.size)
rail = geometry.circle(20).sweep(
    geometry.path([(0, 0, 0), (1000, 0, 500), (1400, 0, 500)], corner=100))
check("a profile sweeps along a path with a rounded corner",
      rail.volume > 0 and abs(rail.size[0] - 1400) < 45, rail.size)
coil = geometry.circle(8).sweep(geometry.helix(100, 150, 450))
check("and along a helix", coil.volume > 0 and 450 <= coil.size[2] < 480,
      coil.size)
try:
    geometry.tube(5, 10, 10)
    check("a tube inside out is refused", False)
except geometry.GeometryError:
    check("a tube inside out is refused", True)


print()
print("a code feature builds the part")

doc, feature = code_part(source(
    "posts = [cylinder(params.size / 4, 100).move(i * params.size * 2)",
    "         for i in range(int(params.n))]",
    "result(union(posts))"))
check("it builds", doc.last_report.ok and not feature.error, feature.error)
one = doc.shape is not None and geometry.Solid(doc.shape).volume
check("  a solid with volume", one and one > 0)

doc.params.set_expression("n", "8")
doc.rebuild()
check("changing a parameter rebuilds the structure, not just its size",
      abs(geometry.Solid(doc.shape).volume - 2 * one) < 1e-3)

doc, feature = code_part("body = box(10, 20, 30)")
check("a variable called body is taken as the answer",
      not feature.error and abs(geometry.Solid(doc.shape).volume - 6000) < 1e-6,
      feature.error)

doc, feature = code_part("x = 1")
check("building nothing is a clear failure", "built nothing" in feature.error,
      feature.error)

doc, feature = code_part(source("a = box(1, 1, 1)", "",
                                "b = a.move(nowhere)", "result(a)"))
check("a script that fails names the line", "line 3" in feature.error,
      feature.error)
check("  and what went wrong", "nowhere" in feature.error, feature.error)

doc, feature = code_part("params.n = 9\nresult(box(1, 1, 1))")
check("a code feature cannot set a parameter",
      "set 'n' from a rule" in feature.error, feature.error)

doc, feature = code_part("result(box(1, 1, 1))", trusted=False)
check("untrusted, it does not run", "trusted" in feature.error, feature.error)
check("  and says how to allow it", "dLogic" in feature.error)

saved = os.path.join(WORK, "posts.pdat")
doc, feature = code_part("result(box(3, 4, 5))")
doc.save(saved)
back = Document.load(saved)
check("a saved code feature comes back with its script",
      back.features[0].source == "result(box(3, 4, 5))")
check("  and opens untrusted, so it does not run",
      "trusted" in back.features[0].error, back.features[0].error)
rules.trust_path(saved)
again = Document.load(saved)
check("allowing the file for the session lets it build",
      not again.features[0].error and again.last_report.ok,
      again.features[0].error)


print()
print("trust comes from this machine, never from the file")

elsewhere = os.path.join(WORK, "shared")
os.makedirs(elsewhere)
kept = os.path.join(elsewhere, "kept.pdat")
doc.save(kept)
check("a file somewhere new is not trusted",
      not rules.document_trusted(Document.load(kept)))
rules.trust_folder(elsewhere)
check("trusting its folder trusts it", rules.document_trusted(
    Document.load(kept)))
check("  and is remembered in the preferences",
      any(os.path.normcase(elsewhere) == os.path.normcase(f)
          for f in prefs.load().trusted_folders))
check("  which a file cannot write to",
      "trusted_folders" not in str(Document.load(kept).to_dict()))
check("a sibling folder with a longer name is not let in",
      not rules.is_trusted_path(elsewhere + "-evil" + os.sep + "x.pdat"))

part = os.path.join(WORK, "inner.pdat")
code_part("result(box(1, 1, 1))")[0].save(part)
asm = AssemblyDocument()
asm.path = os.path.join(WORK, "outer.adat")
asm.place(part)
asm.save(asm.path)
asm = AssemblyDocument.load(asm.path)
trusted = rules.trust_document(asm)
check("trusting an assembly trusts the parts it places",
      rules.is_trusted_path(part), trusted)


print()
print("rules reach the rest of the model")

doc = Document()
doc.params.add("width", "40")
sk = SketchFeature()
sk.name = "Base"
cid = sk.sketch.add_constraint("radius", entities=[
    sk.sketch.add_circle((0, 0), 10)], value=10)
name = sk.sketch.constraints[cid].name
doc.add_feature(sk)
other = SketchFeature()
other.name = "Top"
other.sketch.add_constraint("radius", entities=[
    other.sketch.add_circle((0, 0), 5)], value=5)
doc.add_feature(other)
doc.rebuild()

got = rules.run(rules.Rule(name="Dims", source=source(
    "log(sketches['Base'].dims.%s)" % name,
    "sketches['Base'].dims.%s = 25" % name)), doc)
check("a sketch dimension is read and written", got.ok and "10" in got.output,
      got.error or got.output)
check("  and the change is recorded", got.changed == ["Base.%s" % name],
      got.changed)
check("  and lands on the sketch",
      doc.features[0].sketch.constraints[cid].expression == "25.0")
twice = rules.run(rules.Rule(name="Both", source="dims.%s = 3" % name), doc)
check("a name two sketches share is refused, naming them",
      not twice.ok and "Base" in twice.error and "Top" in twice.error,
      twice.error)

got = rules.run(rules.Rule(name="Props", source=source(
    "props.PartNumber = 'A-%d' % params.width",
    "log(props.PartNumber)")), doc)
check("properties are read and written",
      doc.properties.get("PartNumber") == "A-40", got.error)

got = rules.run(rules.Rule(name="Mat", source='material("Steel, Mild")'), doc)
check("the material can be set", doc.material == "Steel, Mild", got.error)
bad = rules.run(rules.Rule(name="Mat2", source='material("Cheese")'), doc)
check("  but not to one that does not exist", not bad.ok and
      doc.material == "Steel, Mild")

asm = AssemblyDocument()
first = asm.place(part)
second = asm.place(part)
got = rules.run(rules.Rule(name="Comp", source=source(
    "log(len(components))",
    "c = components['%s']" % second.name,
    "c.place(100, 0, 0, rz=90)",
    "components['%s'].suppressed = True" % first.name)), asm)
check("components are moved", got.ok and
      abs(second.placement.position[0] - 100) < 1e-9, got.error)
check("  and turned", abs(abs(second.placement.rotation[2]) - 1.5708) < 1e-3,
      second.placement.rotation)
check("  and suppressed", first.suppressed)
check("a part has no components, and says so quietly",
      rules.run(rules.Rule(name="None", source="log(len(components))"),
                doc).output == "0")


print()
print("triggers")

doc = Document()
doc.params.add("a", "1")
doc.params.add("b", "1")
doc.params.add("seen", "0")
doc.rules.trusted = True
opened = doc.rules.add("Opened", "params.seen = params.seen + 1")
opened.on_rebuild = False
opened.on_open = True
doc.rules.run_event(doc, "on_rebuild")
check("an on-open rule does not run on a rebuild",
      doc.params["seen"].value == 0)
doc.rules.run_event(doc, "on_open")
doc.params.evaluate_all()
check("  but does when the document opens", doc.params["seen"].value == 1)

saving = doc.rules.add("Stamp", "props.Revision = 'B'")
saving.on_rebuild = False
saving.before_save = True
doc.rules.run_event(doc, "before_save")
check("a before-save rule runs before saving",
      doc.properties.get("Revision") == "B")

watch = doc.rules.add("Watch", "params.b = params.a * 10")
watch.on_rebuild = False
watch.watch = ["a"]
doc.rules.run_event(doc, "on_rebuild")          # first look, a baseline
doc.params.evaluate_all()
check("a watching rule waits for its parameter", doc.params["b"].value == 1)
doc.params.set_expression("a", "3")
doc.params.evaluate_all()
out = doc.rules.run_event(doc, "on_rebuild")
doc.params.evaluate_all()
check("  and runs when it changes", doc.params["b"].value == 30,
      [r.summary() for r in out])
out = doc.rules.run_event(doc, "on_rebuild")
check("  and not again while it holds still", not out,
      [r.summary() for r in out])

back = Document()
back.load_dict(doc.to_dict())
kept = {r.name: r for r in back.rules}
check("triggers are saved",
      kept["Opened"].on_open and kept["Stamp"].before_save
      and kept["Watch"].watch == ["a"])
button = doc.rules.add("Ask", 'log("hi")')
button.button = True
check("a rule can be a button", [r.name for r in doc.rules.buttons] == ["Ask"])

untrusted = Document()
untrusted.rules.load(doc.rules.to_list())
check("no trigger fires on an untrusted document",
      untrusted.rules.run_event(untrusted, "on_open") == [])


print()
print("the stair sample")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "examples"))
import build_railing                                          # noqa: E402

stair = build_railing.build()
check("the stair builds", stair.last_report.ok,
      [f.error for f in stair.features if f.error])
tall = geometry.Solid(stair.shape).size[2]
stair.params.set_expression("steps", "16")
stair.rebuild()
check("more steps make a taller stair",
      geometry.Solid(stair.shape).size[2] > tall + 3 * 170)
stair.params.set_expression("sides", "2")
stair.rebuild()
check("and it takes a railing on both sides", stair.last_report.ok and
      geometry.Solid(stair.shape).size[1] > 900)


print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
