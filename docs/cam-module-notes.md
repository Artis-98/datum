# CAM module, target constraints

Notes carried over from the CNC session, so this does not need re-deriving.

## What the machine is

A 1.5 x 3 m belt driven table, originally a plasma cutter, being converted to
also run a 2.2 kW water cooled spindle (ER20, 1 to 13 mm collets, 24000 rpm).
Control is **MyPlasm CNC** by Proma Elektronika.

## What MyPlasm can and cannot do

- It has a milling mode, but only for **flat 2D cutting**. No 3D.
- Its G-code support accepts **2D paths only**. The system is built around
  importing a 2D drawing, not around consuming CAM output.
- It **can** do multiple depth passes on its own.
- Its built in CAM (MyMiniCAM) caps tip size at **5 mm**, so it can offset by
  at most 2.5 mm. That is not enough for a 6 mm cutter, which needs 3 mm.

## What that means for DATUM

**Export DXF, not G-code.** MyPlasm wants 2D paths and does its own depth
stepping, so the postprocessor stays trivial. No feeds, no speeds, no Z moves.

**Apply cutter compensation ourselves.** The exported geometry must already be
the toolpath centreline, because MyPlasm's own offset cannot handle our cutter
sizes. The operator then imports with Offset switched **off**.

**Inside versus outside matters per profile.** Outer profiles offset outward by
tool radius, holes and slots inward. A slot narrower than the cutter is an
error worth catching at generation time rather than at the machine.

## Scope

In: closed profile offsetting, inside/outside, lead in and lead out, cut
ordering (holes before the outer profile, or parts fall out early), DXF import
and export, toolpath drawn in the viewport.

Out: pocketing strategies, adaptive clearing, 3D surfacing, machine
simulation, tool databases. Those turn a module into a decade.

## Why OCCT rather than Clipper

The kernel already does robust 2D offsetting including arcs. A polygon based
offsetter would force everything to line segments first and lose arc fidelity,
which matters on a 300 mm circle.

## Reference geometry

`C:\Users\aa\Downloads\CLAUDE\cnc-calibration\` holds a machine calibration
test pattern with the offset applied by hand, plus the generator script. Useful
as a correctness check: DATUM's CAM output for the same input should match it.
