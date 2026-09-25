# Excavator

A tracked excavator: 24 parts, 48 components, one assembly. The tote next
door is the sample that shows the CAM side - a real sheet, a real cutter,
a real DXF - and it is a box. This is the one that shows what the modeller
carries: plate, castings, glass, rubber and polished rod, every part its
own file with its own material, and a linkage that reaches where the
geometry says it does.

    ../build_excavator.py          builds everything in this folder
    ../../tests/shot_excavator.py  opens it and takes the pictures
    ../../tests/test_excavator.py  checks it still goes together

Nothing here was drawn by hand or nudged into place. The script goes
through the same core API the buttons call, so every file opens, edits and
rebuilds normally.

![the excavator](assembly.png)

## What it is

4.7 m over the bucket, 2.5 m tall, tracks 2.1 m long at 1.2 m gauge.

| part | off | material | appearance |
|------|-----|----------|------------|
| Pivot Pin | 11 | Steel, Mild | Steel, Polished |
| Bucket Tooth | 5 | Steel, Mild | Steel, Mill Finish |
| Ram Barrel | 3 | Steel, Mild | Paint, Machine Charcoal |
| Ram Rod | 3 | Steel, Mild | Steel, Polished |
| Blade Arm, Bucket Link | 2 each | Steel, Mild | Paint, Machine Charcoal |
| Drive Sprocket, Idler Wheel | 2 each | Steel, Mild | Paint, Machine Charcoal |
| Rubber Track | 2 | Rubber, EPDM | Rubber, Black |
| Work Light | 2 | ABS | Plastic, Matte Black |
| Boom, Arm, Bucket | 1 each | Steel, Mild | Paint, Machine Yellow |
| House, Cab Frame, Dozer Blade | 1 each | Steel, Mild | Paint, Machine Yellow |
| Counterweight, Track Frame | 1 each | Steel, Mild | Paint, Machine Charcoal |
| Cab Glazing | 1 | Glass | Glass, Clear |
| Slew Ring, Exhaust Stack | 1 each | Steel, Mild | Steel, Mill Finish |
| Seat | 1 | ABS | Plastic, Matte Black |
| Bucket Ram Barrel, Bucket Ram Rod | 1 each | Steel, Mild | charcoal, polished |

It masses out at 12 tonnes, which is heavier than a real machine this size
and is not a mistake: most of it is modelled as solid section where a real
one is fabricated from plate. Only the house, the hood and the track frame
are hollowed. The number is what the material densities say about the
solid that is actually there, which is the useful thing for it to say.

Two appearances were added to the library for this: **Paint, Machine
Yellow** and **Paint, Machine Charcoal**. Every part still takes its
density from a normal material - the yellow ones are all Steel, Mild - so
painting something a different colour changes nothing about what it
weighs. That separation is the whole reason materials and appearances are
two lists and not one.

![the linkage](linkage.png)

## The pose is worked out, not typed in

Three angles decide the machine: the boom 38 degrees up, the arm 60 down
from the boom tip, the bucket curled to 25. Everything after that is
asked for rather than measured:

    boom_at    = Placement(BOOM_PIVOT, turn_y(-BOOM_ANGLE))
    arm_pivot  = boom_at.apply_point((2260, 0, 232))
    arm_at     = Placement(arm_pivot, turn_y(-ARM_ANGLE))
    bucket_pivot = arm_at.apply_point((1250, 0, 0))

`(2260, 0, 232)` is where the boom's own drawing puts its tip bore, so the
arm hangs off the hole that is really there. Change `BOOM_ANGLE` by a
degree and the arm, the bucket, the linkage, all three rams and eleven
pins move with it, because none of them has a coordinate of its own.

The whole upper works is built square to the world and then swung 18
degrees, by composing one rotation about Z onto the outside of every
placement in the group. That is also why the house, the cab, the boom and
its rams all agree about where the machine is pointing.

## A ram is two parts aimed at each other

A hydraulic ram is a barrel and a rod, and they are not constrained
together. The barrel is placed at one mount looking at the other, the rod
at the other mount looking back, and how far the rod is inside the barrel
is whatever the distance between the mounts leaves over - which is what a
real one does. No ram here had its stroke worked out by hand.

The build prints the check:

    Boom Ram       929 mm between eyes, piston  149 mm into the tube  ok
    Arm Ram       1335 mm between eyes, piston  555 mm into the tube  ok
    Bucket Ram     578 mm between eyes, piston  118 mm into the tube  ok

If a pose were changed far enough that a piston left its tube, that line
would say so instead of quietly drawing a rod hanging in mid-air. It is
also why there are two sizes of ram: the bucket's is less than half the
boom's, and one cylinder will not stretch that far.

## The bucket is a solid with the inside taken out

![the bucket](part-bucket.png)

The outline is the bucket's whole silhouette, mouth closed off, extruded
620 wide. The pocket is a second profile cut 520 wide, so 50 mm of side
plate stays at each end. The trick is that the pocket's last edge sits
*outside* the solid: that is what opens the mouth, rather than leaving a
lid on it.

## The track lugs are walked round the perimeter

![the undercarriage](undercarriage.png)

The belt is a stadium - two lines and two half circles - with the inside
cut out of it. The lugs are 39 slots at 122 mm pitch, placed by walking
the outside of that loop and asking where a given distance along it lands
and which way is out. Walking it is what lets one pitch carry round the
ends and along the straights without the corners needing lugs of their
own shape.

![the machine from the side](side.png)
