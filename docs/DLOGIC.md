# dLogic

dLogic is DATUM's answer to iLogic: Python that lives inside a document
and drives it. There are two kinds of script, and they do different jobs.

| | a **rule** | a **code feature** |
|---|---|---|
| lives in | any document: part, assembly, drawing, CAM sheet | a part's feature tree, like an extrude |
| runs | when you ask, or on a trigger | on every rebuild |
| does | changes things: parameters, dimensions, components, properties, material | builds a solid |
| can ask | yes, with a window full of sliders | no |

Open the panel with **Manage → dLogic** (parts), or the dLogic button on
the Assemble, Drawing and CAM tabs. A code feature is **Manage → Code
Feature**, or the Code feature button at the bottom of the panel.

**Nobody has to remember a name.** Both editors list everything the
script can reach down the right side: the document's parameters with
their values, sketch dimensions, features, components and properties, then
every command. Double-click one and it goes in at the cursor. Type in the
box above the list to find something. In the editor, a dot or
**Ctrl+Space** offers the names that fit. **F5** or **Ctrl+Enter** runs
it, and a script that fails has its line lit up in red.

The fastest way to learn it is to open `examples/railing/Stair
Railing.pdat`, allow it, and read its two code features and two rules.

## Your first rule

New rule, then:

```python
params.width = params.height * 2
log("width is now", params.width)
```

Press **Run**. Whatever `log` says appears under the editor. Tick **after
every rebuild** and from then on changing `height` anywhere moves `width`
with it.

## What a rule can reach

| name | what it is |
|---|---|
| `params` | parameters by name: `params.width`, `params["width"]` |
| `dims` | sketch dimensions by name: `dims.d1` |
| `sketches` | sketches by name, for a dimension two of them share |
| `features` | the feature tree by name |
| `components` | an assembly's components |
| `props` | the document's properties: `props.PartNumber` |
| `material(...)` | read the material, or set it |
| `appearance(...)` | read the appearance, or set it |
| `form`, `slider`, `number`, `choice`, `note` | windows, below |
| `log(...)` | output, under the editor and in the status bar |
| `math` | the standard module: `math.sqrt`, `math.pi`, `math.ceil`... |
| `units` | `"mm"` or `"in"` |
| `doc` | the document itself, for anything the shorthands miss |

### Parameters

Reading one gives its value. Assigning sets it and the model rebuilds:

```python
log(params.width)            # 60.0
params.width = 120           # a number
params.width = "height * 2"  # an expression, which keeps following
```

A name that does not exist yet is created rather than refused, so a rule
can bring its own parameters with it:

```python
if "clearance" not in params:
    params.clearance = 0.2
```

Writing back the value a parameter already has is not a change, so a rule
that keeps something in proportion settles instead of looping.

### Sketch dimensions

Every dimension in a sketch has a name, d1, d2 and so on, shown on the
dimension itself. Two sketches can both have a d1, so there are two ways
in:

```python
sketches["Base"].dims.d1 = 40     # always works
dims.d1 = 40                      # while only one sketch has a d1
```

If more than one sketch has that name, `dims` refuses and names the
sketches, rather than changing whichever it found first.

### Features

```python
features["Counterbore"].suppressed = params.thickness < 6
features.suppress("Gusset", params.length < 120)
log(", ".join(features.names()))
```

### Components

In an assembly, `components` is every placed component:

```python
bolt = components["Bolt:1"]
bolt.move(10, 0, 0)               # shift it
bolt.place(0, 0, 50, rz=90)       # put it somewhere, turned 90 about Z
bolt.suppressed = True            # out of the assembly, not deleted
bolt.visible = False              # still there, not drawn
log(bolt.position, bolt.path)

for c in components.like("Bolt"): # every component whose name has "Bolt"
    c.visible = params.show_bolts > 0

log(len(components), "components")
```

In a part `components` is empty, so a rule written for both runs in both.

### Properties, material, appearance

```python
props.PartNumber = "BR-%d" % params.length
props.Description = "Bracket, %d long" % params.length
material("Steel, Mild")
appearance("Paint, Machine Yellow")
log(material())
```

A material or appearance that is not in the library is refused.

## Windows with sliders

A rule can ask a question:

```python
form("Block size",
     note("Drag to size the block."),
     slider("width", 20, 200, 5),
     slider("height", 10, 120, 5))
```

That opens a window with two sliders on it. **The model moves while you
drag.** Cancel puts every parameter back where it was, so trying
something costs nothing.

```python
slider("width", 20, 200, 5)      # parameter, lowest, highest, step
number("depth")                  # a box you type into
choice("size", ["10", "20", "30"])
choice("material", ["Steel, Mild", "Aluminium 6061"])   # sets the material
note("Anything you want to say.")
```

`form` gives back `True` if OK was pressed:

```python
if form("Size", slider("width", 20, 200)):
    log("settled on", params.width)
```

**Forms that stay.** Tick **Show as a button** on a rule and it sits at the
top of the dLogic panel as a button, one click from its window. That is
how a part becomes a small configurator.

**Forms never open by themselves.** A rule that runs because something
happened does not stop to ask: a model that opens a window every time it
rebuilds is a model you cannot work in. There `form` quietly answers
`False`.

## When rules run

Each rule says when, at the bottom of its editor:

- **Run**, **Run all**, or its button: whenever you ask.
- **after every rebuild**
- **when the document opens**, once it is allowed to run
- **before saving**, so a rule can stamp a revision or a description onto
  exactly what is written
- **when these change**: a list of parameters. The rule runs when one of
  their values changes, and not otherwise.

A rule that changes something gets exactly one rebuild out of it, never a
loop.

## Code features

A parameter can make a box wider. It cannot decide that a staircase has
fourteen steps rather than twelve, put a baluster on each one and run a
handrail along the top, because that is not a size, it is a structure,
and a structure is a loop. A code feature is that loop: a feature in the
tree whose solid is whatever its script builds, rebuilt whenever a
parameter moves.

```python
"""A row of posts on a plate."""
post = cylinder(10, 120)
row = post.repeat(int(params.n), x=60)
plate = box(60 * params.n, 40, 10).move(-30, -20, -10)
result(plate + row)
```

Hand the solid back with `result(...)`, or leave it in a variable called
`body`. The feature joins it, cuts it, intersects it or keeps it as a new
body, like any other feature. Parameters can be read but not set: a
feature that changed what the rebuild was reading would never settle. Use
a rule for that.

The editor does not rebuild on every keystroke, because half-typed code is
broken code. **Build** (F5) runs it and shows the result in the model.

### The toolkit

Units are the document's, millimetres unless it says otherwise. Angles
are degrees.

**Solids**

| | |
|---|---|
| `box(x, y, z)` | from the origin along +X +Y +Z; `centered=True` to centre it |
| `cylinder(r, h)` | standing on the origin along Z; `axis="x"` to lay it down |
| `cone(r1, r2, h)`, `sphere(r)`, `torus(R, r)` | |
| `tube(outer, inner, h)` | a pipe |
| `rod(start, end, r)` | a round bar between two points: balusters, pins, braces |
| `beam(start, end, w, h)` | a square bar between two points, standing upright |

**Profiles and paths.** A profile is a flat outline on XY about the
origin. It becomes a solid by extruding, revolving or sweeping.

| | |
|---|---|
| `circle(r)`, `rect(w, h)`, `regular(sides, r)` | |
| `polygon([(x, y), ...])` | any outline, by its corners |
| `.extrude(h)`, `.extrude(h, taper=5)` | up along Z |
| `.revolve(360, axis="y")` | turned about an axis in its plane |
| `.sweep(path)` | carried along a path, kept square to it |
| `path([(x, y, z), ...], corner=r)` | straight lines, rounded at each bend |
| `arc(start, middle, end)` | through three points |
| `helix(r, pitch, h)` | a coil about Z: springs, threads |

**Operations.** Every one gives a new solid and leaves the original alone,
so one solid can be the template for a hundred copies.

| | |
|---|---|
| `a + b`, `a - b`, `a & b` | join, cut, keep what they share |
| `union(list)` | everything in a list as one; far faster than adding one at a time |
| `.move(x, y, z)`, `.move_to(x, y, z)` | |
| `.rotate("z", 45)`, `.rotate("z", 45, about=(x, y, z))` | |
| `.mirror("yz")`, `.scale(2)` | |
| `.repeat(n, x=100)` | n copies, each a step further on |
| `.repeat_around(n, "z")` | n copies round an axis |
| `.fillet(r)`, `.chamfer(d)` | every edge |
| `.volume`, `.size`, `.bbox`, `.centre` | measuring as you go |

### The staircase

`examples/railing/Stair Railing.pdat` is a part that is almost all code.
Its parameters are steps, rise, going, width, tread, rail_height,
baluster_gap and sides. Two code features build it:

- **Stair**: a tread per step with a nosing, and two stringers cut level
  with the floor.
- **Railing**: newel posts on the first and last tread, as many balusters
  per tread as it takes to stay under `baluster_gap`, and a handrail swept
  up the pitch that turns level into the top post. `sides = 2` puts one on
  each side.

Two rules drive it. **Size** is a button with a slider for every
dimension. **Keep it comfortable** watches rise, going and steps, checks
the old two-rises-and-a-going rule, and writes the description. Change
`steps` and every tread, baluster and the rail are worked out again.
`examples/build_railing.py` is the script that made it, if you would
rather read it there.

## Nothing runs until you allow it

A document carrying code can run it on whatever machine opens it. iLogic
has that problem and it is a real one. Here a file arrives untrusted every
time: its code features show as failed, the dLogic panel opens and says
why, and nothing runs until you press **Allow**. Only allow code from
someone you trust.

- **Allow** trusts this document for this session. For an assembly it
  trusts the parts it places too.
- **Always for this folder** trusts everything in the document's folder
  from now on. That list lives in your own preferences, never in a file,
  because the file is not the one who gets to decide.
- Rules and code features you write yourself are trusted as you write
  them.

Being straight about the rest: the short list of names a script is given
is not a sandbox and does not pretend to be one. Python cannot be made
into a sandbox from the inside. The list is there to keep scripts
readable. The trust gate is what protects you.

## Worked example: a bracket

A shelf bracket that keeps its proportions, numbers itself, and loses its
gusset when it gets small. Tick **Show as a button**:

```python
"""Bracket proportions."""

if "length" not in params:
    params.length = 120
if "thickness" not in params:
    params.thickness = 6

form("Bracket",
     note("The gusset appears once the bracket is long enough to need it."),
     slider("length", 60, 300, 10),
     slider("thickness", 3, 12, 1),
     choice("material", ["Steel, Mild", "Aluminium 6061"]))

params.height = params.length * 0.6
params.hole = params.thickness * 1.5

if "Gusset" in features:
    features.suppress("Gusset", params.length < 120)

props.PartNumber = "BR-%d-%d" % (params.length, params.thickness)
log("%.0f x %.0f, %.0f thick" % (params.length, params.height,
                                 params.thickness))
```
