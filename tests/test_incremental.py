"""Rebuilding only what changed must build exactly what a full rebuild does.

A rebuild now puts back the result of every feature whose inputs did not
change instead of building it again. That is only a speedup if it can
never be told apart from building everything, so every sample part is put
through every kind of edit, and after each one the incremental result is
compared with a from-scratch rebuild of the same definition: the same
volume, the same faces, the same bodies, the same errors.
"""
import glob
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WORK = tempfile.mkdtemp(prefix="datum_incremental_")
os.environ["DATUM_CONFIG_DIR"] = os.path.join(WORK, "cfg")

from datum.core import document as document_module, kernel, rules  # noqa
from datum.core.document import Document                           # noqa
from datum.core.features import PrimitiveFeature                   # noqa

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def summary(doc):
    shape = doc.shape
    if shape is None:
        body = None
    else:
        volume = kernel.volume(shape)
        body = (round(volume, 3), len(kernel.faces(shape)),
                len(kernel.edges(shape)))
    return (body, [b.name for b in doc.bodies],
            [(f.id, f.error) for f in doc.features])


def full(doc):
    """The same definition, built from nothing with reuse switched off."""
    fresh = Document()
    fresh.load_dict(json.loads(json.dumps(doc.to_dict())))
    fresh.rules.trusted = True
    fresh.rollback_index = doc.rollback_index
    was = document_module.REUSE_RESULTS
    document_module.REUSE_RESULTS = False
    try:
        fresh.rebuild()
    finally:
        document_module.REUSE_RESULTS = was
    return summary(fresh)


def counted(doc):
    calls = []
    for feature in doc.features:
        original = type(feature).build

        def build(ctx, _f=feature, _o=original):
            calls.append(_f.id)
            return _o(_f, ctx)
        feature.build = build
    return calls


parts = sorted(glob.glob(os.path.join(ROOT, "examples", "**", "*.pdat"),
                         recursive=True))
for path in parts:
    rules.trust_path(path)
edits = 0

print("every sample part, through every kind of edit")
for path in parts:
    name = os.path.basename(path)
    doc = Document.load(path)
    doc.rules.trusted = True
    doc.rebuild()
    ok = summary(doc) == full(doc)

    calls = counted(doc)
    doc.rebuild()
    idle = not calls

    # every parameter, nudged and put back
    for param in list(doc.params.names()):
        before = doc.params[param].expression
        try:
            value = float(doc.params[param].value)
        except (TypeError, ValueError):
            continue
        doc.params.set_expression(param, repr(value * 1.1 + 1.0))
        doc.rebuild()
        ok = ok and summary(doc) == full(doc)
        doc.params.set_expression(param, before)
        doc.rebuild()
        ok = ok and summary(doc) == full(doc)
        edits += 2

    # every feature suppressed and brought back
    for feature in doc.features:
        feature.suppressed = not feature.suppressed
        doc.rebuild()
        ok = ok and summary(doc) == full(doc)
        feature.suppressed = not feature.suppressed
        doc.rebuild()
        ok = ok and summary(doc) == full(doc)
        edits += 2

    # rolled back half way and forward again
    if len(doc.features) > 2:
        doc.rollback_index = len(doc.features) // 2
        doc.rebuild()
        ok = ok and summary(doc) == full(doc)
        doc.rollback_index = None
        doc.rebuild()
        ok = ok and summary(doc) == full(doc)
        edits += 2

    # an undo and a redo of a real edit
    doc.push_undo()
    extra = PrimitiveFeature()
    extra.kind = "box"
    extra.a, extra.b, extra.c = "5", "5", "5"
    extra.operation = "cut"
    doc.add_feature(extra)
    doc.rebuild()
    ok = ok and summary(doc) == full(doc)
    doc.undo()
    doc.rebuild()
    ok = ok and summary(doc) == full(doc)
    doc.redo()
    doc.rebuild()
    ok = ok and summary(doc) == full(doc)
    edits += 3

    check("%s: identical to a full rebuild throughout" % name, ok)
    check("  and a rebuild with nothing changed builds nothing", idle,
          len(calls))

print("  (%d edits compared)" % edits)


print()
print("only what changed is built")

doc = Document()
doc.params.add("width", "40")
doc.params.add("hole", "5")
base = PrimitiveFeature()
base.kind = "box"
base.a, base.b, base.c = "width", "30", "20"
base.operation = "new"
doc.add_feature(base)
middle = PrimitiveFeature()
middle.kind = "box"
middle.a, middle.b, middle.c = "10", "10", "40"
middle.operation = "join"
doc.add_feature(middle)
last = PrimitiveFeature()
last.kind = "cylinder"
last.a, last.b, last.c = "hole", "50", "0"
last.origin = ("20", "15", "-5")
last.operation = "cut"
doc.add_feature(last)
doc.rebuild()
calls = counted(doc)

doc.params.set_expression("hole", "6")
doc.rebuild()
check("a parameter only the last feature uses rebuilds only that feature",
      calls == [last.id], calls)
del calls[:]
doc.params.set_expression("width", "45")
doc.rebuild()
check("one the first feature uses rebuilds from there on",
      calls == [base.id, middle.id, last.id], calls)
check("  and gives what a full rebuild gives", summary(doc) == full(doc))
del calls[:]
middle.suppressed = True
doc.rebuild()
check("suppressing a feature rebuilds what follows it",
      calls == [last.id], calls)
check("  correctly", summary(doc) == full(doc))
del calls[:]
middle.suppressed = False
doc.rebuild()
check("bringing it back rebuilds it, or puts it back",
      summary(doc) == full(doc))

broken = PrimitiveFeature()
broken.kind = "box"
broken.a, broken.b, broken.c = "nonsense_name", "1", "1"
broken.operation = "join"
doc.add_feature(broken)
doc.rebuild()
first_error = broken.error
calls = counted(doc)
doc.rebuild()
check("a failed feature keeps its error when put back",
      broken.error == first_error and first_error and not calls,
      (broken.error, calls))
check("  and still reports it", any(fid == broken.id
                                    for fid, _e in doc.last_report.errors))


print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
