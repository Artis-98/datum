# dLogic

A rule is a piece of Python that belongs to a document. It reads the
parameters, works something out, writes them back, and the model rebuilds
around the answer. Parts, assemblies, drawings and CAM sheets all carry
them and all run them the same way.

Open the panel with **Manage → dLogic**. The document is at the top, its
rules underneath. Double-click one to edit it.

## Your first rule

New rule, then:

```python
params.width = params.height * 2
log("width is now", params.width)
```

Press **Run**. Whatever `log` says appears in the box underneath. Press
OK, and from then on the rule runs every time the document rebuilds, so
changing `height` anywhere moves `width` with it.

That is the whole idea. Everything below is detail.

## What a rule can see

| name | what it is |
|---|---|
| `params` | the parameters, by name: `params.width`, `params["width"]` |
| `doc` | the document itself, for anything the shorthands miss |
| `features` | the feature tree by name |
| `log(...)` | prints into the box under the editor |
| `math` | the standard library module |
| `units` | `"mm"` or `"in"` |
| `form`, `slider`, `number`, `choice`, `note` | windows, below |

Reading a parameter gives you its value as a number. Assigning to one
sets it and the model rebuilds:

```python
log(params.width)            # 60.0
params.width = 120           # sets it
params.width = "height * 2"  # an expression works too
```

A name that does not exist yet is created rather than refused, so a rule
can bring its own parameters with it:

```python
if "clearance" not in params:
    params.clearance = 0.2
```

Features can be suppressed, which is how a rule turns a feature off for
one size of part and on for another:

```python
features["Counterbore"].suppressed = params.thickness < 6
log(", ".join(features.names()))
```

## Windows with sliders

This is the part people actually want. A rule can ask a question:

```python
form("Block size",
     note("Drag to size the block."),
     slider("width", 20, 200, 5),
     slider("height", 10, 120, 5))
```

That opens a window with two sliders on it. **The model moves while you
drag.** Press Cancel and every parameter goes back to where it was, so
trying something costs nothing.

The controls:

```python
slider("width", 20, 200, 5)      # parameter, lowest, highest, step
number("depth")                  # a box you type into
choice("material", ["Steel, Mild", "Aluminium 6061"])
note("Anything you want to say.")
```

`form` gives back `True` if OK was pressed and `False` if it was
cancelled, so a rule can do something with the answer:

```python
if form("Size", slider("width", 20, 200)):
    log("settled on", params.width)
else:
    log("left alone")
```

A form named `material` in a `choice` sets the document's material
rather than a parameter, because that is what somebody picking from a
list of materials means.

**Forms never open by themselves.** A rule that runs because the document
rebuilt does not stop to ask a question: a model that opens a window
every time it rebuilds is a model you cannot work in. Run the rule
yourself and the window appears. Let it run on a rebuild and the `form`
call quietly says nobody answered.

## When rules run

- **Run** in the editor, or **Run all** in the panel: immediately.
- **After every rebuild**, if the rule's checkbox says so. Turn that off
  for a rule that should only ever run when asked.

A rule that changes a parameter gets exactly one extra rebuild out of it.
Writing back the value a parameter already has is not a change, so a rule
that keeps something in proportion settles instead of looping.

## Nothing runs until you allow it

A document carrying rules can run code on whatever machine opens it.
iLogic has that problem and it is a real one. Here a file arrives
untrusted every time, however often you have opened it: the panel says so
across the top and nothing runs until you press **Allow**. Trust is per
document and per session, and it is never saved into the file, because
the file is not the one who gets to decide.

Rules you write yourself are trusted as you write them.

Being straight about the rest: the short list of names a rule is given is
not a sandbox and does not pretend to be one. Python cannot be made into
a sandbox from the inside. The list is there to make the useful names
obvious. The thing that protects you is the trust gate.

## Worked example

A shelf bracket that keeps its proportions and loses its gusset when it
gets small:

```python
"""Bracket proportions."""

if "length" not in params:
    params.length = 120
if "thickness" not in params:
    params.thickness = 6

form("Bracket",
     note("The gusset appears once the bracket is long enough to need it."),
     slider("length", 60, 300, 10),
     slider("thickness", 3, 12, 1))

params.height = params.length * 0.6
params.hole = params.thickness * 1.5

if "Gusset" in features:
    features["Gusset"].suppressed = params.length < 120

log("%.0f x %.0f, %.0f thick" % (params.length, params.height,
                                 params.thickness))
```
