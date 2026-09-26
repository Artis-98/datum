"""dLogic: rules that drive a document.

The two things worth holding still are that a rule can actually move the
model, and that it cannot move anything until somebody says it may.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WORK = tempfile.mkdtemp(prefix="datum_rules_")
os.environ["DATUM_CONFIG_DIR"] = os.path.join(WORK, "cfg")

from datum.core import kernel, rules                          # noqa: E402
from datum.core.assembly import AssemblyDocument              # noqa: E402
from datum.core.cam import CamDocument                        # noqa: E402
from datum.core.document import Document                      # noqa: E402
from datum.core.drawing import DrawingDocument                # noqa: E402
from datum.core.features import PrimitiveFeature              # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def box(width=40.0, height=10.0):
    doc = Document()
    doc.params.add("width", str(width))
    doc.params.add("height", str(height))
    feature = PrimitiveFeature()
    feature.kind = "box"
    feature.a, feature.b, feature.c = "width", "30", "height"
    feature.operation = "new"
    feature.name = "Box"
    doc.add_feature(feature)
    doc.rebuild()
    return doc


def source(*lines):
    return "\n".join(lines)


print("a rule reads the parameters and writes them back")

doc = box()
rule = rules.Rule(name="Rule0", source=source(
    'log("width is", params.width)',
    'params.width = params.height * 5',
))
result = rules.run(rule, doc)
check("it ran", result.ok, result.error)
check("it read what was there", "width is 40.0" in result.output,
      result.output)
check("it wrote what it worked out", doc.params["width"].value == 50.0,
      doc.params["width"].value)
check("and said which parameter it touched", result.changed == ["width"],
      repr(result.changed))

# Writing the value that is already there is not a change, on purpose: a
# rule that runs after every rebuild and sets a parameter to what it
# already is would otherwise ask for another rebuild every time.
again = rules.run(rule, doc)
check("writing the same value again is not a change", again.changed == [],
      repr(again.changed))

doc.params.set_expression("height", "25")
rules.run(rule, doc)
doc.rebuild()
check("the model is built from what the rule set",
      abs(kernel.bounding_box(doc.shape)[3] - 125.0) < 1e-6,
      kernel.bounding_box(doc.shape)[3])

new = rules.run(rules.Rule(name="Fresh", source='params.depth = 12'), doc)
check("a parameter a rule invents is created rather than refused",
      new.ok and "depth" in doc.params, new.error)


print()
print("a rule that does not work says so, and takes nothing down with it")

broken = rules.run(rules.Rule(name="Broken", source=source(
    'log("about to fail")',
    'params.nothing_here + 1',
)), doc)
check("it is reported as failed", not broken.ok)
check("with the line it failed on", broken.line == 2, broken.line)
check("and what it said before failing", "about to fail" in broken.output,
      broken.output)

bad_syntax = rules.run(rules.Rule(name="Syntax", source="if :"), doc)
check("a rule that will not compile is caught the same way",
      not bad_syntax.ok and bad_syntax.line == 1,
      (bad_syntax.error, bad_syntax.line))

check("the document is untouched by either",
      doc.params["width"].value == 125.0, doc.params["width"].value)


print()
print("features are reachable too")

suppressed = rules.run(rules.Rule(name="Off", source=source(
    'features["Box"].suppressed = True',
    'log(", ".join(features.names()))',
)), doc)
check("a rule can suppress a feature",
      suppressed.ok and doc.features[0].suppressed, suppressed.error)
check("and list them", "Box" in suppressed.output, suppressed.output)
doc.features[0].suppressed = False


print()
print("nothing runs until the document is trusted")

doc = box()
doc.rules.add("Rule0", 'params.width = 999')
check("a fresh rule set is not trusted", not doc.rules.trusted)
check("so running them all does nothing",
      doc.rules.run_all(doc) == [] and doc.params["width"].value == 40.0,
      doc.params["width"].value)

doc.rules.trusted = True
ran = doc.rules.run_all(doc)
check("once trusted they run", len(ran) == 1 and ran[0].ok)
check("and the parameter moved", doc.params["width"].value == 999.0,
      doc.params["width"].value)

doc.rules.rules[0].enabled = False
doc.params.set_expression("width", "40")
doc.rules.run_all(doc)
check("a disabled rule stays out of it", doc.params["width"].value == 40.0,
      doc.params["width"].value)

doc.rules.rules[0].enabled = True
doc.rules.rules[0].on_rebuild = False
doc.rules.run_all(doc, only_on_rebuild=True)
check("so does one marked manual under a rebuild",
      doc.params["width"].value == 40.0, doc.params["width"].value)
check("but not when asked directly",
      doc.rules.run_all(doc) and doc.params["width"].value == 999.0,
      doc.params["width"].value)


print()
print("a rule can ask, with a window")

asked = {}


def fake_form(document, title, controls):
    asked["title"] = title
    asked["kinds"] = [c.kind for c in controls]
    asked["params"] = [c.param for c in controls if c.param]
    # answer it the way somebody dragging a slider would
    document.params.set_expression("width", "77")
    return True


doc = box()
rules.show_form = fake_form
try:
    result = rules.run(rules.Rule(name="Ask", source=source(
        'ok = form("Size",',
        '          note("drag me"),',
        '          slider("width", 20, 200, 5),',
        '          choice("material", ["Steel, Mild"]))',
        'log("answered", ok, "width", params.width)',
    )), doc)
    check("the rule ran", result.ok, result.error)
    check("the window was asked for", asked.get("title") == "Size", asked)
    check("with the controls it named",
          asked.get("kinds") == ["label", "slider", "choice"], asked)
    check("and the answer reached the model",
          doc.params["width"].value == 77.0, doc.params["width"].value)
    check("which the rule could see", "width 77.0" in result.output,
          result.output)

    # a rebuild must never stop to open a window
    doc.rules.add("Asks", 'log("shown", form("Size", slider("width", 1, 2)))')
    doc.rules.trusted = True
    quiet = doc.rules.run_all(doc, only_on_rebuild=True)
    check("a rebuild does not open one",
          quiet and "not shown" in quiet[0].output, quiet[0].output if quiet
          else "nothing ran")
    loud = doc.rules.run_all(doc)
    check("but asking directly does",
          loud and "shown True" in loud[0].output,
          loud[0].output if loud else "nothing ran")
finally:
    rules.show_form = None

check("with no interface at all it is a quiet no",
      "not shown" in rules.run(rules.Rule(
          name="Headless", source='log(form("x"))'), doc).output)


print()
print("every kind of document carries them, and none carries trust")

for cls in (Document, AssemblyDocument, DrawingDocument, CamDocument):
    held = cls()
    held.rules.add("Rule0", 'log("hello")')
    held.rules.trusted = True
    back = cls()
    back.load_dict(held.to_dict())
    check("%s saves its rules" % cls.__name__,
          [r.name for r in back.rules] == ["Rule0"],
          [r.name for r in back.rules])
    check("  and the source with them",
          list(back.rules)[0].source == 'log("hello")')
    check("  but never the trust",
          not back.rules.trusted,
          "a file must not be able to trust itself")


print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
