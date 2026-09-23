"""3Dconnexion SpaceMouse support.

The device is read straight from its HID interface rather than through
3DxWare, so no vendor driver or SDK is required - the puck works as soon as
it is plugged in.  Reads are non-blocking and polled from a Qt timer on the
GUI thread, which keeps the whole thing free of cross-thread trouble.

Report layout (all 3Dconnexion pucks):

* report ``1`` - translation, three signed 16-bit little-endian axes.  Newer
  firmware packs all six axes into this one report instead.
* report ``2`` - rotation, three signed 16-bit little-endian axes.
* report ``3`` - button bitmask.

A wireless receiver publishes *one multi-axis collection per pairing slot*,
so a Universal Receiver shows up as several identical-looking interfaces of
which only the paired one ever sends anything.  Every matching interface is
therefore opened and drained together; which of them is live is the puck's
business, not ours.

There is a second way in.  When 3DxWare is installed it can take the device
into its own mode, and then the HID interfaces open perfectly and stay
silent forever - which looks exactly like a puck nobody is touching.  For
that case Windows has ``TDxInput.Device``, the COM interface 3DxWare itself
publishes, which reports the same six axes *through* the driver instead of
around it.  Both backends run at once: only one of them is ever live, so
they cannot fight, and whichever is talking drives the camera.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from PySide6 import QtCore

VENDOR_IDS = (0x256F, 0x046D)          # 3Dconnexion, and older Logitech units
MULTI_AXIS_USAGE_PAGE = 0x01
MULTI_AXIS_USAGE = 0x08

FULL_SCALE = 350.0                     # raw counts at full deflection
POLL_MS = 16                           # ~60 Hz
RESCAN_MS = 3000
AXES = ("tx", "ty", "tz", "rx", "ry", "rz")


def available() -> bool:
    """True when the hidapi binding is importable."""
    try:
        import hid  # noqa: F401
    except Exception:
        return False
    return True


def find_devices() -> List[Dict[str, Any]]:
    """Every 3Dconnexion multi-axis interface currently attached."""
    try:
        import hid
    except Exception:
        return []

    out = []
    try:
        entries = hid.enumerate()
    except Exception:
        return []

    for entry in entries:
        if entry.get("vendor_id") not in VENDOR_IDS:
            continue
        if (entry.get("usage_page") == MULTI_AXIS_USAGE_PAGE
                and entry.get("usage") == MULTI_AXIS_USAGE):
            out.append(entry)
    return out


def describe(entry: Dict[str, Any]) -> str:
    """A short human label for one interface of one device."""
    name = entry.get("product_string") or "3Dconnexion device"
    interface = entry.get("interface_number")
    return name if interface in (None, -1) else "%s #%d" % (name, interface)


# --------------------------------------------------------------- backends


TDX_PROGID = "TDxInput.Device"
TDX_FULL_SCALE = 1.0          # smallest magnitude treated as full deflection
TDX_TURN_FLOOR = 1e-4         # a turn this small has no meaningful axis


def tdx_available() -> bool:
    """True when 3DxWare's COM interface can be used."""
    try:
        import comtypes.client  # noqa: F401
    except Exception:
        return False
    try:
        import winreg
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, TDX_PROGID))
    except Exception:
        return False
    return True


class TdxSensor:
    """The six axes as 3DxWare's own COM interface reports them.

    Kept deliberately dumb: connect, read, disconnect.  Every failure is
    swallowed into "not connected", because this is the optional path and an
    app must not fall over because a driver is half installed.
    """

    def __init__(self) -> None:
        self.device = None
        self.sensor = None
        self.product = ""
        self.peak = 0.0           # largest raw magnitude seen, for tuning
        self.peak_turn = 0.0      # and the largest turn, for diagnostics

    @property
    def is_open(self) -> bool:
        return self.sensor is not None

    def open(self) -> bool:
        if self.sensor is not None:
            return True
        if not tdx_available():
            return False
        try:
            import comtypes.client

            device = comtypes.client.CreateObject(TDX_PROGID)
            device.Connect()
            self.sensor = device.Sensor
            self.device = device
            try:
                self.product = "3DxWare device 0x%04X" % int(device.Type)
            except Exception:
                self.product = "3DxWare device"
            return True
        except Exception:
            self.sensor = None
            self.device = None
            return False

    def close(self) -> None:
        try:
            if self.device is not None:
                self.device.Disconnect()
        except Exception:
            pass
        self.device = None
        self.sensor = None

    def read(self) -> Optional[List[float]]:
        """Six axes in DATUM's order, or None when there is nothing to say.

        The COM frame is X right, Y up, Z out of the screen towards you,
        with rotation as an axis and an angle.  DATUM wants (right, away,
        up) and (pitch, roll, yaw), so Y and Z swap and the push axis
        changes sign - pushing the puck away is a positive zoom here and a
        negative Z there.

        The two rotation axes that feel like each other's job - tilting the
        puck forward and twisting it - are X and Y, and they cross on the
        way through: what the COM interface reports about its X axis is the
        twist DATUM calls rz, and what it reports about Y is the pitch
        DATUM calls rx.  Taking them straight across instead tilts the model
        left and right when you push the puck forward, which is how this was
        found.  Y also runs the other way round from DATUM's pitch, so it is
        negated here rather than left for the reverse tick-box to fix.
        """
        if self.sensor is None:
            return None
        try:
            t = self.sensor.Translation
            r = self.sensor.Rotation
            tx, ty, tz = float(t.X), float(t.Y), float(t.Z)
            ax, ay, az = float(r.X), float(r.Y), float(r.Z)
            angle = float(r.Angle)
        except Exception:
            self.close()
            return None

        # A rotation given as an axis and an angle has no usable axis at
        # dead centre: the angle is nothing and the direction it is about is
        # whatever the noise says.  Only that degenerate case is cut.
        self.peak_turn = max(self.peak_turn, abs(angle))
        if abs(angle) < TDX_TURN_FLOOR:
            angle = 0.0

        rx, ry, rz = -ay * angle, az * angle, ax * angle
        raw = (tx, -tz, ty, rx, ry, rz)
        self.peak = max(self.peak, max(abs(v) for v in raw))
        if not any(raw):
            return None
        # 3Dconnexion never wrote down what full deflection reads as here,
        # and it differs between drivers, so the largest magnitude yet seen
        # sets the scale.  Guessing too small only costs a moment of being
        # over-eager, which the clamp downstream absorbs and the next firm
        # push corrects; guessing too large would make the puck feel dead,
        # which is the failure this whole backend exists to avoid.
        scale = max(TDX_FULL_SCALE, self.peak)
        return [v / scale for v in raw]


@dataclass
class SpaceMouseSettings:
    enabled: bool = True
    sensitivity: float = 1.0
    deadzone: float = 0.06
    pan_speed: float = 1.0
    zoom_speed: float = 1.0
    rotate_speed: float = 1.0
    dominant_axis: bool = False        # snap to the single strongest axis
    invert: Dict[str, bool] = field(default_factory=lambda: {a: False
                                                             for a in AXES})

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "sensitivity": self.sensitivity,
            "deadzone": self.deadzone,
            "pan_speed": self.pan_speed,
            "zoom_speed": self.zoom_speed,
            "rotate_speed": self.rotate_speed,
            "dominant_axis": self.dominant_axis,
            "invert": dict(self.invert),
        }

    def load(self, data: Dict[str, Any]) -> None:
        for key in ("enabled", "dominant_axis"):
            if key in data:
                setattr(self, key, bool(data[key]))
        for key in ("sensitivity", "deadzone", "pan_speed", "zoom_speed",
                    "rotate_speed"):
            if key in data:
                setattr(self, key, float(data[key]))
        for axis, value in (data.get("invert") or {}).items():
            if axis in self.invert:
                self.invert[axis] = bool(value)


@dataclass
class Interface:
    """One opened HID collection, and what it has sent us."""

    path: bytes
    label: str
    device: Any
    frames: int = 0


class SpaceMouse(QtCore.QObject):
    """Polls every SpaceMouse interface and reports six-axis motion."""

    moved = QtCore.Signal(float, float, float, float, float, float)
    button_pressed = QtCore.Signal(int)
    connected = QtCore.Signal(str)
    disconnected = QtCore.Signal()

    def __init__(self, settings: Optional[SpaceMouseSettings] = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.settings = settings or SpaceMouseSettings()
        self.interfaces: List[Interface] = []
        self.tdx = TdxSensor()
        self.product = ""

        # diagnostics, so "nothing moves" can be told apart from "nothing
        # arrives" without attaching a debugger to a user's machine
        self.frames = 0
        self.tdx_frames = 0
        self.live_label = ""                    # interface that last spoke
        self.unknown_reports: Dict[int, int] = {}
        self.last_frame: Optional[Tuple[int, bytes]] = None

        self._state = [0.0] * 6
        self._buttons = 0
        self._idle_frames = 0

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)

        self._rescan = QtCore.QTimer(self)
        self._rescan.setInterval(RESCAN_MS)
        self._rescan.timeout.connect(self._try_open)

    # ------------------------------------------------------------- lifecycle

    def start(self) -> bool:
        """Begin polling, retrying quietly until a device shows up."""
        opened_tdx = self.tdx.open()
        if not available() and not opened_tdx:
            return False
        self._try_open()
        self._timer.start()
        self._rescan.start()
        if opened_tdx and not self.interfaces:
            self.connected.emit(self.summary())
        return True

    def stop(self) -> None:
        self._timer.stop()
        self._rescan.stop()
        self.tdx.close()
        self._close_all()

    @property
    def is_connected(self) -> bool:
        return bool(self.interfaces) or self.tdx.is_open

    @property
    def device(self):
        """The first open interface, for callers that only want one."""
        return self.interfaces[0].device if self.interfaces else None

    def _try_open(self) -> None:
        """Open every matching interface that is not open already.

        The rescan keeps running after the first success on purpose: a
        wireless puck that is switched on later, or paired into a different
        slot of the receiver, appears as a new interface rather than as
        traffic on the one already open.
        """
        entries = find_devices()
        if not entries:
            return

        import hid

        known = {i.path for i in self.interfaces}
        first = not self.interfaces
        for entry in entries:
            path = entry.get("path")
            if path in known:
                continue
            device = hid.device()
            try:
                device.open_path(path)
                device.set_nonblocking(True)
            except Exception:
                # another application may hold this one; the rest still work
                try:
                    device.close()
                except Exception:
                    pass
                continue
            self.interfaces.append(
                Interface(path=path, label=describe(entry), device=device))
            if not self.product:
                self.product = (entry.get("product_string")
                                or "3Dconnexion device")

        if first and self.interfaces:
            self.connected.emit(self.summary())

    def _drop(self, interface: Interface) -> None:
        try:
            interface.device.close()
        except Exception:
            pass
        if interface in self.interfaces:
            self.interfaces.remove(interface)
        if not self.is_connected:
            self.disconnected.emit()

    def _close_all(self) -> None:
        had = bool(self.interfaces)
        for interface in list(self.interfaces):
            try:
                interface.device.close()
            except Exception:
                pass
        self.interfaces = []
        if had and not self.is_connected:
            self.disconnected.emit()

    def summary(self) -> str:
        """What is open, said in one line."""
        parts = []
        if self.interfaces:
            parts.append("%s, %d HID interface(s)"
                         % (self.product, len(self.interfaces)))
        if self.tdx.is_open:
            parts.append("3DxWare (%s)" % self.tdx.product)
        return " + ".join(parts) or "No 3Dconnexion device found"

    def traffic(self) -> str:
        """What has actually arrived, for the settings dialog."""
        if not self.is_connected:
            return "Nothing open."
        if not self.frames and not self.tdx_frames:
            return ("Open, but no data yet - move the puck. If nothing ever "
                    "appears, run tests/probe_spacemouse.py.")
        parts = []
        if self.tdx_frames:
            parts.append("%d via 3DxWare (peak %.2f)"
                         % (self.tdx_frames, self.tdx.peak))
        if self.frames:
            parts.append("%d raw from %s" % (self.frames, self.live_label))
        line = "  |  ".join(parts)
        if self.unknown_reports:
            line += "  |  unrecognised report(s): %s" % ", ".join(
                "%d x%d" % (rid, n)
                for rid, n in sorted(self.unknown_reports.items()))
        return line

    # ---------------------------------------------------------------- polling

    def _poll(self) -> None:
        if not self.settings.enabled or not self.is_connected:
            return

        # 3DxWare first: when it is running it owns the puck, and whatever it
        # reports is the truth.
        tdx = self.tdx.read()

        # The raw interfaces are drained every poll even when their data is
        # going to be thrown away, and that is not housekeeping - it is the
        # whole bug.  Packets nobody reads do not evaporate: they queue in
        # the OS.  Skipping the drain while 3DxWare was talking meant the
        # queue filled for as long as the gesture lasted, and the instant
        # 3DxWare went quiet - which is the instant the puck is released -
        # the lot arrived in one go and replayed the gesture that had just
        # been made, in the raw report's axis order rather than the COM
        # one's, so it came out sideways.  Longer gesture, longer replay.
        consume = tdx is None and not self.tdx_frames
        got_data = self._drain(consume)

        if tdx is not None:
            self.tdx_frames += 1
            self._idle_frames = 0
            self._state = tdx
        elif got_data and consume:
            self._idle_frames = 0
        else:
            # The puck stops sending once it is centred, so decay to zero
            # rather than leaving the last motion latched on.  A raw HID
            # interface gets a couple of frames of grace, because silence
            # there can be a dropped packet.  3DxWare is asked rather than
            # listened to, so its silence is an answer: the puck is centred.
            self._idle_frames += 1
            grace = 0 if self.tdx_frames else 2
            if self._idle_frames > grace and any(self._state):
                self._state = [0.0] * 6

        values = self._shaped()
        if any(abs(v) > 1e-9 for v in values):
            self.moved.emit(*values)

    def _drain(self, consume: bool) -> bool:
        """Empty every raw interface's queue, using the data or discarding it.

        The cap is generous because the point is to leave nothing behind; a
        queue that is only partly emptied each poll still grows.
        """
        got = False
        for interface in list(self.interfaces):
            for _ in range(64):
                try:
                    data = interface.device.read(64)
                except Exception:
                    self._drop(interface)
                    break
                if not data:
                    break
                got = True
                if not consume:
                    continue
                interface.frames += 1
                self.frames += 1
                self.live_label = interface.label
                self._consume(data)
        return got

    def _consume(self, data: List[int]) -> None:
        report = data[0]
        payload = bytes(data[1:])
        self.last_frame = (report, payload[:16])

        if report == 1:
            if len(payload) >= 12:             # six axes in one report
                axes = struct.unpack("<6h", payload[:12])
                self._state = [v / FULL_SCALE for v in axes]
            elif len(payload) >= 6:
                axes = struct.unpack("<3h", payload[:6])
                self._state[0:3] = [v / FULL_SCALE for v in axes]
        elif report == 2 and len(payload) >= 6:
            axes = struct.unpack("<3h", payload[:6])
            self._state[3:6] = [v / FULL_SCALE for v in axes]
        elif report == 3 and payload:
            mask = int.from_bytes(payload[:4], "little")
            newly = mask & ~self._buttons
            self._buttons = mask
            for bit in range(32):
                if newly & (1 << bit):
                    self.button_pressed.emit(bit)
        else:
            self.unknown_reports[report] = self.unknown_reports.get(
                report, 0) + 1

    def _shaped(self) -> Tuple[float, ...]:
        """Apply deadzone, inversion, response curve and sensitivity."""
        s = self.settings
        out = []
        for name, raw in zip(AXES, self._state):
            value = max(-1.0, min(1.0, raw))
            if abs(value) < s.deadzone:
                value = 0.0
            else:
                # rescale past the deadzone so motion starts from zero, then
                # square it to keep fine control near the centre
                span = 1.0 - s.deadzone
                value = (abs(value) - s.deadzone) / span * (1 if value > 0 else -1)
                value = value * abs(value)
            if s.invert.get(name):
                value = -value
            out.append(value * s.sensitivity)

        if s.dominant_axis:
            strongest = max(range(6), key=lambda i: abs(out[i]))
            out = [v if i == strongest else 0.0 for i, v in enumerate(out)]

        return tuple(out)
