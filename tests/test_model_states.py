"""Model states: one part, several variations, each remembered.

A state keeps how it differs from Primary: parameter values, suppression,
properties and material.  Changes made while it is active are its own, a
feature added belongs to every state, and an assembly can place a part in
any of its states.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401

from datum.core import kernel                                     # noqa: E402
from datum.core.assembly import AssemblyDocument                  # noqa: E402
from datum.core.document import Document                          # noqa: E402
from datum.core.features import PrimitiveFeature                  # noqa: E402
from datum.core.modelstates import PRIMARY                        # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + ("  " + str(extra) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def near(a, b, tol=1e-3):
    return abs(a - b) < tol


doc = Document()
doc.properties["PartNumber"] = "BOLT-20"
bolt = PrimitiveFeature(name="Shank")
bolt.kind = "cylinder"
bolt.a, bolt.b = "5", "20"
doc.add_feature(bolt)
head = PrimitiveFeature(name="Head")
head.kind = "cylinder"
head.a, head.b = "8", "5"
doc.add_feature(head)
doc.rebuild()
length = bolt.param_names["b"]

print("making a state")
states = doc.model_states
check("a part starts with Primary only", states.names() == [PRIMARY])
made = states.create(doc, "Long")
check("a new state is made and active", made == "Long"
      and states.active == "Long")
doc.set_parameter(length, "40")
head.suppressed = True
doc.properties["PartNumber"] = "BOLT-40"
doc.rebuild()
check("changes while it is active build",
      near(kernel.bounding_box(doc.shape)[5], 40.0))

print()
print("switching")
states.activate(doc, PRIMARY)
doc.rebuild()
check("Primary is the part it was", bolt.b == "20" and not head.suppressed
      and doc.properties["PartNumber"] == "BOLT-20",
      (bolt.b, head.suppressed, doc.properties))
states.activate(doc, "Long")
doc.rebuild()
check("and Long is what was changed in it", bolt.b == "40"
      and head.suppressed and doc.properties["PartNumber"] == "BOLT-40",
      (bolt.b, head.suppressed, doc.properties))

extra = PrimitiveFeature(name="Washer")
extra.kind = "cylinder"
extra.a, extra.b = "10", "2"
doc.add_feature(extra)
states.activate(doc, PRIMARY)
check("a feature added in a state is in every state",
      any(f.name == "Washer" for f in doc.features))

print()
print("saving, and placing a state")
path = os.path.join(tempfile.mkdtemp(prefix="datum_states_"), "bolt.pdat")
doc.rebuild()
doc.save(path)
again = Document.load(path)
check("the states are saved", again.model_states.names()
      == [PRIMARY, "Long"], again.model_states.names())
again.model_states.activate(again, "Long")
check("and keep what they change", again.features[0].b == "40",
      again.features[0].b)

asm = AssemblyDocument()
asm.path = os.path.join(os.path.dirname(path), "pair.adat")
short = asm.place(path)
long_one = asm.place(path)
long_one.model_state = "Long"
asm.rebuild()
heights = sorted(kernel.bounding_box(o.shape)[5] - kernel.bounding_box(
    o.shape)[2] for o in asm.occurrences)
check("an assembly places the same part in two states",
      near(heights[0], 20.0, 0.05) and near(heights[1], 40.0, 0.05),
      heights)

print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
