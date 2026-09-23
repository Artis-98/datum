r"""Show exactly what a 3Dconnexion device is sending, if anything.

Run it, then move the puck:

    .venv\Scripts\python.exe tests\probe_spacemouse.py

It opens every multi-axis interface at once and prints each report as it
arrives, so it tells apart the three ways this goes wrong: the device is not
found, it is found but another program holds it, or it is open and silent.
"""

import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datum.ui import spacemouse                                    # noqa: E402

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0


def tdx_probe(seconds: float) -> int:
    """Watch 3DxWare's own COM interface, which works while it is running."""
    print("== 3DxWare COM interface ==")
    if not spacemouse.tdx_available():
        print("  not available (comtypes missing, or 3DxWare not installed)")
        return 0

    sensor = spacemouse.TdxSensor()
    if not sensor.open():
        print("  registered, but would not connect")
        return 0
    print("  connected: %s" % sensor.product)
    print("  move the puck, %.0f seconds..." % seconds)

    frames, shown, started = 0, 0, time.time()
    while time.time() - started < seconds:
        values = sensor.read()
        if values:
            frames += 1
            if shown < 12:
                shown += 1
                print("    " + "  ".join(
                    "%s %+7.3f" % (n, v)
                    for n, v in zip(("Tx", "Ty", "Tz", "Rx", "Ry", "Rz"),
                                    values)))
        time.sleep(0.02)
    print("  frames: %d, peak magnitude %.3f" % (frames, sensor.peak))
    if frames:
        print("  -> this is the backend that works here")
    sensor.close()
    return frames


def main() -> int:
    seconds = SECONDS
    live = tdx_probe(min(seconds, 12.0))
    print()
    print("== raw HID ==")

    if not spacemouse.available():
        print("hidapi is not installed in this environment.")
        return 0 if live else 1

    import hid

    entries = spacemouse.find_devices()
    print("  multi-axis interfaces found: %d" % len(entries))
    for entry in entries:
        print("  vid=%04x pid=%04x  %s"
              % (entry["vendor_id"], entry["product_id"],
                 spacemouse.describe(entry)))
    if not entries:
        print()
        print("Nothing matched. Either the device is unplugged, or its")
        print("receiver is switched off. Vendor ids looked for: %s"
              % ", ".join("%04x" % v for v in spacemouse.VENDOR_IDS))
        return 0 if live else 1

    opened = []
    for entry in entries:
        device = hid.device()
        try:
            device.open_path(entry["path"])
            device.set_nonblocking(True)
        except Exception as exc:
            print("  CANNOT OPEN %s: %s" % (spacemouse.describe(entry), exc))
            continue
        opened.append((spacemouse.describe(entry), device))

    if not opened:
        print()
        print("Every interface refused to open, which means another program")
        print("is holding the device. 3DxWare is the usual one.")
        return 0 if live else 1

    print()
    print("  move the puck again, %.0f seconds, Ctrl-C to stop." % seconds)
    print()

    counts, started = {}, time.time()
    try:
        while time.time() - started < seconds:
            for label, device in opened:
                try:
                    data = device.read(64)
                except Exception as exc:
                    print("read error on %s: %s" % (label, exc))
                    continue
                if not data:
                    continue
                report, payload = data[0], bytes(data[1:])
                counts[(label, report)] = counts.get((label, report), 0) + 1
                axes = ""
                if report in (1, 2) and len(payload) >= 6:
                    count = 6 if len(payload) >= 12 else 3
                    values = struct.unpack("<%dh" % count,
                                           payload[:count * 2])
                    axes = "  " + " ".join("%+6d" % v for v in values)
                print("%-28s report %-2d  %-26s%s"
                      % (label, report, payload[:8].hex(" "), axes))
            time.sleep(0.005)
    except KeyboardInterrupt:
        pass

    print()
    if not counts:
        print("  open, but nothing arrived at all.")
        if live:
            print("  that is expected here - 3DxWare has the puck, and DATUM")
            print("  reads it through the COM interface above.")
            return 0
        print("  either the puck was never moved, or 3DxWare has taken it")
        print("  into its own mode without answering on COM either.")
        return 1

    print("frames received:")
    for (label, report), n in sorted(counts.items()):
        print("  %-28s report %-2d  %d" % (label, report, n))
    live = {label for (label, _r) in counts}
    print()
    print("Live interface(s): %s" % ", ".join(sorted(live)))
    if len(opened) > len(live):
        print("The other %d interface(s) stayed silent, which is normal for a"
              % (len(opened) - len(live)))
        print("receiver - only the paired slot ever sends.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
