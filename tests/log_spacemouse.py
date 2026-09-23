r"""Record what the puck sends and what DATUM makes of it.

Run it, do the gesture that misbehaves, then stop:

    .venv\Scripts\python.exe tests\log_spacemouse.py

It polls the same backend DATUM uses, at the same rate, and writes every
frame to spacemouse-log.txt next to this file: the raw numbers 3DxWare
hands over, then each stage of DATUM's own processing.  At the end it
prints the tail of each gesture and flags any frame where the output grew
while the input was shrinking - which is exactly the "it kicks as I reach
centre" complaint, in numbers.

Nothing here reads DATUM's settings, so close DATUM first or the two will
be polling the same sensor.
"""

import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datum.ui import spacemouse                                    # noqa: E402
from datum.ui.spacemouse import SpaceMouse, SpaceMouseSettings     # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "spacemouse-log.txt")
SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0


def size(values):
    return math.sqrt(sum(v * v for v in values))


def main() -> int:
    sensor = spacemouse.TdxSensor()
    if not sensor.open():
        print("3DxWare's COM interface would not open.")
        print("If DATUM is running, close it and try again.")
        return 1
    print("connected: %s" % sensor.product)

    # the same object DATUM uses, so the shaping and the release gate that
    # run here are the ones that run in the application
    mouse = SpaceMouse(SpaceMouseSettings())

    print()
    print("Recording for %.0f seconds. Do the gesture that goes wrong:" % SECONDS)
    print("  turn the puck, then let it come back to centre slowly.")
    print("Ctrl-C to stop early.")
    print()

    frames = []
    started = time.time()
    try:
        while time.time() - started < SECONDS:
            now = time.time() - started
            raw = None
            try:
                t = sensor.sensor.Translation
                r = sensor.sensor.Rotation
                raw = (float(t.X), float(t.Y), float(t.Z),
                       float(r.X), float(r.Y), float(r.Z), float(r.Angle))
            except Exception as exc:
                print("sensor read failed: %s" % exc)
                break

            mapped = sensor.read()
            if mapped is None:
                mouse._state = [0.0] * 6
                shaped = mouse._shaped()
                out = mouse._settling(shaped)
            else:
                mouse._state = list(mapped)
                shaped = mouse._shaped()
                out = mouse._settling(shaped)

            frames.append((now, raw, mapped, shaped, out))
            time.sleep(spacemouse.POLL_MS / 1000.0)
    except KeyboardInterrupt:
        pass
    sensor.close()

    with open(LOG, "w", encoding="utf-8") as handle:
        handle.write("time     |      raw translation      |       raw axis"
                     "        | angle    | mapped rotation           |"
                     " out rotation\n")
        for now, raw, mapped, shaped, out in frames:
            handle.write(
                "%7.3f | %7.3f %7.3f %7.3f | %6.3f %6.3f %6.3f | %8.5f | "
                "%7.4f %7.4f %7.4f | %7.4f %7.4f %7.4f\n"
                % (now, raw[0], raw[1], raw[2], raw[3], raw[4], raw[5],
                   raw[6],
                   *(list(mapped[3:]) if mapped else [0.0, 0.0, 0.0]),
                   *list(out[3:])))

    moving = [f for f in frames if f[1][6] or size(f[1][:3])]
    print("%d frames, %d of them with the puck off centre" %
          (len(frames), len(moving)))
    print("written to %s" % LOG)

    # the thing being hunted: output going up while input comes down
    print()
    print("frames where the model turned more than the puck was asking")
    flagged = 0
    for i in range(1, len(frames)):
        prev_in, now_in = frames[i - 1][1][6], frames[i][1][6]
        prev_out = size(frames[i - 1][4][3:])
        now_out = size(frames[i][4][3:])
        if now_in < prev_in and now_out > prev_out * 1.5 and now_out > 1e-6:
            flagged += 1
            if flagged <= 12:
                print("  t=%7.3f  angle %.5f -> %.5f   out %.5f -> %.5f"
                      % (frames[i][0], prev_in, now_in, prev_out, now_out))
    print("  %d such frame(s)" % flagged)

    print()
    print("the last 12 frames of the strongest gesture")
    if moving:
        peak = max(range(len(frames)), key=lambda i: abs(frames[i][1][6]))
        end = peak
        while end + 1 < len(frames) and abs(frames[end][1][6]) > 1e-9:
            end += 1
        for i in range(max(0, end - 11), min(len(frames), end + 4)):
            now, raw, mapped, shaped, out = frames[i]
            print("  t=%7.3f angle %9.5f  axis %6.3f %6.3f %6.3f   "
                  "out %8.5f %8.5f %8.5f"
                  % (now, raw[6], raw[3], raw[4], raw[5],
                     out[3], out[4], out[5]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
