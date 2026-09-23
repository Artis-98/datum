"""Sketch nesting / sharing in the browser, and SpaceMouse handling."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


from PySide6 import QtCore, QtWidgets  # noqa: E402

from datum.core import kernel  # noqa: E402
from datum.core.features import ExtrudeFeature, HoleFeature, SketchFeature  # noqa: E402
from datum.core.sketch import STANDARD_PLANES, Sketch  # noqa: E402
from datum.ui import spacemouse  # noqa: E402
from datum.ui.browser import ROLE_ID, ROLE_INDEX, ROLE_KIND, ROLE_SHARED  # noqa: E402
from datum.ui.main_window import MainWindow  # noqa: E402
from datum.ui.panels import SpaceMouseDialog  # noqa: E402
from datum.ui.spacemouse import SpaceMouse, SpaceMouseSettings  # noqa: E402
from datum.ui.theme import stylesheet  # noqa: E402

FAILS = []
app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
win = MainWindow()
win.resize(1400, 880)
win.show()
app.processEvents()


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def pump(n=3):
    for _ in range(n):
        app.processEvents()


def root():
    return win.browser.topLevelItem(0)


def top_rows():
    r = root()
    return [(r.child(i).data(0, ROLE_KIND), r.child(i).text(0))
            for i in range(r.childCount())]


def find_row(name):
    r = root()
    for i in range(r.childCount()):
        if r.child(i).text(0).startswith(name):
            return r.child(i)
    return None


def add_sketch(name, build, plane=None):
    f = SketchFeature()
    f.name = name
    f.sketch = Sketch(plane or STANDARD_PLANES["XY"], name)
    build(f.sketch)
    win.document.add_feature(f)
    f.sketch.name = f.name
    win.rebuild()
    return f


# ==========================================================================
print("a consumed sketch nests under its feature")
win.new_document(prompt=False)
profile = add_sketch("Outline", lambda s: s.add_rectangle((0, 0), (60, 40)))
win.new_feature(ExtrudeFeature)
win.select_all_profiles()
dlg = win._active_dialog
dlg.distance.set_text("10")
dlg.commit()
pump()

rows = top_rows()
names = [t for k, t in rows if k == "feature"]
check("sketch no longer sits at the top level", "Outline" not in names, names)
check("extrude is at the top level", any("Extrude" in n for n in names), names)

extrude_row = find_row("Extrude")
check("extrude has one child", extrude_row.childCount() == 1,
      extrude_row.childCount() if extrude_row else "no row")
child = extrude_row.child(0)
check("the child is the sketch", child.text(0).startswith("Outline"),
      child.text(0))
check("nested sketch keeps its document index",
      child.data(0, ROLE_INDEX) == win.document.index_of(profile.id),
      child.data(0, ROLE_INDEX))
check("nested sketch is not flagged shared",
      not child.data(0, ROLE_SHARED))

print("an unconsumed sketch stays at the top")
loose = add_sketch("Spare", lambda s: s.add_circle((80, 20), 6))
win.browser.refresh()
check("unconsumed sketch is top level",
      any(t.startswith("Spare") for k, t in top_rows() if k == "feature"),
      top_rows())

print("sharing brings it back to the top as well")
win.toggle_share(profile.id)
pump()
names = [t for k, t in top_rows() if k == "feature"]
check("shared sketch reappears at the top level",
      any(n.startswith("Outline") for n in names), names)

shared_row = find_row("Outline")
check("top-level copy is marked shared", bool(shared_row.data(0, ROLE_SHARED)))
extrude_row = find_row("Extrude")
check("it still stays nested under the feature",
      extrude_row.childCount() == 1, extrude_row.childCount())
check("the nested copy is NOT marked shared",
      not extrude_row.child(0).data(0, ROLE_SHARED))
check("both rows point at the same feature",
      shared_row.data(0, ROLE_ID) == extrude_row.child(0).data(0, ROLE_ID))

print("unsharing hides the top-level copy again")
win.toggle_share(profile.id)
pump()
names = [t for k, t in top_rows() if k == "feature"]
check("shared row removed", not any(n.startswith("Outline") for n in names),
      names)
check("still nested", find_row("Extrude").childCount() == 1)

print("a second consumer shares the sketch automatically")
hole_sketch = add_sketch("Hole Centres", lambda s: s.add_circle((30, 20), 3))
win.new_feature(HoleFeature)
d = win._active_dialog
d.sketch.setCurrentIndex(d.sketch.findData(hole_sketch.id))
d.diameter.set_text("6")
d.commit()
pump()
check("hole sketch nests under the hole",
      find_row("Hole") is not None and find_row("Hole").childCount() == 1,
      find_row("Hole").childCount() if find_row("Hole") else "none")

second = ExtrudeFeature()
second.sketch_id = hole_sketch.id
second.distance = "4"
second.operation = "join"
win.document.add_feature(second)
win.rebuild()
check("a sketch used twice counts as shared",
      win.browser.is_shared(win.document.feature(hole_sketch.id)))
names = [t for k, t in top_rows() if k == "feature"]
check("auto-shared sketch shows at the top level",
      any(n.startswith("Hole Centres") for n in names), names)

print("End of Part still lands in the right place with nesting")
win.set_rollback(1)
pump()
r = root()
kinds = [r.child(i).data(0, ROLE_KIND) for i in range(r.childCount())]
check("marker present", "end" in kinds, kinds)
marker_at = kinds.index("end")
above = [r.child(i).data(0, ROLE_INDEX) for i in range(marker_at)
         if r.child(i).data(0, ROLE_KIND) == "feature"]
below = [r.child(i).data(0, ROLE_INDEX)
         for i in range(marker_at + 1, r.childCount())
         if r.child(i).data(0, ROLE_KIND) == "feature"]
# a nested sketch travels with its feature, so it can legitimately have an
# index below the rollback while sitting under a feature above it
check("everything above the marker is built", all(i < 1 for i in above), above)
check("everything below the marker is rolled back",
      all(i >= 1 for i in below), below)
win.set_rollback(None)
pump()

print("dragging the marker uses document indices, not row numbers")
r = root()
marker = None
for i in range(r.childCount()):
    if r.child(i).data(0, ROLE_KIND) == "end":
        marker = r.child(i)
check("marker index maps to the end of the tree",
      win.browser._feature_row(marker) == len(win.document.features),
      win.browser._feature_row(marker))

extrude_row = find_row("Extrude")
check("a top-level row reports its document index",
      win.browser._feature_row(extrude_row)
      == win.document.index_of(int(extrude_row.data(0, ROLE_ID))),
      win.browser._feature_row(extrude_row))

print("save / load keeps the shared flag")
import tempfile
from datum.core.document import Document

path = os.path.join(tempfile.mkdtemp(prefix="forge_share_"), "p.pdat")
win.toggle_share(profile.id)
path = win.document.save(path)
reloaded = Document.load(path)
check("shared flag survives a round trip",
      reloaded.feature(profile.id).shared is True)

# ==========================================================================
print("SpaceMouse")
check("hidapi is installed", spacemouse.available())
devices = spacemouse.find_devices()
check("a multi-axis device is present", len(devices) > 0,
      "%d found" % len(devices))
if devices:
    print("       device: %s" % devices[0].get("product_string"))

check("the app opened the device", win.spacemouse.is_connected,
      "product=%r" % win.spacemouse.product)

# A wireless receiver publishes one multi-axis collection per pairing slot
# and only the paired one ever sends, so opening just the first is how the
# puck ends up connected and silent.
check("every multi-axis interface was opened",
      len(win.spacemouse.interfaces) == len(devices),
      "%d of %d" % (len(win.spacemouse.interfaces), len(devices)))
check("and each one only once",
      len({i.path for i in win.spacemouse.interfaces})
      == len(win.spacemouse.interfaces))
before = len(win.spacemouse.interfaces)
win.spacemouse._try_open()
check("a rescan does not open them again",
      len(win.spacemouse.interfaces) == before,
      len(win.spacemouse.interfaces))


print("3DxWare's COM interface is the other way in")
check("it is detectable", isinstance(spacemouse.tdx_available(), bool))


class _Fake:
    """Stands in for the COM sensor, which needs a driver and a hand."""

    class _V:
        def __init__(self, x, y, z, angle=0.0):
            self.X, self.Y, self.Z, self.Angle = x, y, z, angle
            self.Length = max(abs(x), abs(y), abs(z))

    def __init__(self, t, r):
        self.Translation = self._V(*t)
        self.Rotation = self._V(*r)


sensor = spacemouse.TdxSensor()
sensor.sensor = _Fake((0.5, 0.0, 0.0), (0.0, 0.0, 0.0, 0.0))
check("sliding right is Tx", sensor.read() == [0.5, 0, 0, 0, 0, 0],
      sensor.read())

# COM says X right, Y up, Z towards you; DATUM wants right, away, up
sensor.sensor = _Fake((0.0, 0.0, -0.8), (0.0, 0.0, 0.0, 0.0))
check("pushing away is a positive Ty", sensor.read()[1] > 0, sensor.read())
sensor.sensor = _Fake((0.0, 0.7, 0.0), (0.0, 0.0, 0.0, 0.0))
check("lifting is a positive Tz", sensor.read()[2] > 0, sensor.read())

# X and Y cross on the way through: taking them straight across tilts the
# model left and right when the puck is pushed forward, and rolls it forward
# when the puck is twisted - each doing the other's job
sensor.sensor = _Fake((0, 0, 0), (1.0, 0.0, 0.0, 0.4))
check("rotation about the COM X axis is twist",
      abs(sensor.read()[5] - 0.4) < 1e-9, sensor.read())
sensor.sensor = _Fake((0, 0, 0), (0.0, 1.0, 0.0, 0.4))
check("about COM Y is pitch, and runs the other way",
      abs(sensor.read()[3] + 0.4) < 1e-9, sensor.read())
sensor.sensor = _Fake((0, 0, 0), (0.0, 0.0, 1.0, 0.4))
check("about COM Z - towards you - is roll",
      abs(sensor.read()[4] - 0.4) < 1e-9, sensor.read())

sensor.sensor = _Fake((0, 0, 0), (0.0, 0.0, 0.0, 0.0))
check("a centred puck says nothing at all", sensor.read() is None,
      sensor.read())
check("the peak is remembered for tuning", sensor.peak >= 0.8, sensor.peak)


print("a turn too small to have a direction is not a turn")
# The axis of an axis-and-angle rotation is meaningless once the angle is
# nearly nothing, so the degenerate case is cut at the sensor.
tiny = spacemouse.TdxSensor()
tiny.sensor = _Fake((0, 0, 0), (0.0, 1.0, 0.0, 0.5))
check("a real turn reads", tiny.read() is not None)
tiny.sensor = _Fake((0.4, 0, 0), (0.31, 0.62, 0.72, 1e-9))
values = tiny.read()
check("a turn with no direction left in it is dropped",
      values is not None and not any(abs(v) > 1e-12 for v in values[3:]),
      values)
check("and dropping it does not drop the slide with it",
      values is not None and abs(values[0]) > 0.3, values)


print("the interface nobody is reading is still filling up")
# This is the one that made the model replay a gesture sideways the moment
# the puck was let go.  While 3DxWare was talking, the raw interfaces were
# skipped - and their packets did not go away, they queued.  The instant
# 3DxWare went quiet the whole queue arrived at once, in the raw report's
# axis order, and played the gesture back.


class _Queued:
    """A HID interface holding a backlog of packets."""

    def __init__(self, packets):
        self.packets = list(packets)
        self.reads = 0

    def read(self, _size):
        self.reads += 1
        return self.packets.pop(0) if self.packets else []

    def close(self):
        pass


import struct as _struct                                          # noqa: E402

backlog = [[2] + list(_struct.pack("<3h", 0, 0, 300)) for _ in range(40)]
queued = _Queued(backlog)
rig = SpaceMouse(SpaceMouseSettings())
rig.interfaces = [spacemouse.Interface(path=b"x", label="raw", device=queued)]
# a lift over COM, which is DATUM's tz; the backlog is all twist, which is
# rz, so whichever one comes out says which backend was believed
rig.tdx.sensor = _Fake((0.0, 0.5, 0.0), (0, 0, 0, 0))

sent = []
rig.moved.connect(lambda *v: sent.append(v))
rig._poll()
check("the COM reading is the one used",
      sent and abs(sent[-1][2]) > 0.1 and abs(sent[-1][5]) < 1e-12,
      sent[-1] if sent else None)
check("and the backlog was emptied all the same", not queued.packets,
      len(queued.packets))
check("without being counted as input", rig.frames == 0, rig.frames)

# now let go: 3DxWare goes quiet, and there must be nothing left to replay
rig.tdx.sensor = _Fake((0, 0, 0), (0, 0, 0, 0))
sent.clear()
for _ in range(4):
    rig._poll()
check("letting go replays nothing", not sent, sent)
check("and the model is left where it was", not any(rig._state), rig._state)
rig.tdx.sensor = None
rig.interfaces = []


quiet = SpaceMouse(SpaceMouseSettings())
check("nothing open reads as nothing open",
      quiet.traffic() == "Nothing open.", quiet.traffic())
check("and the summary says so",
      "No 3Dconnexion" in quiet.summary(), quiet.summary())
quiet.tdx.sensor = _Fake((0, 0, 0), (0, 0, 0, 0))
check("an open backend counts as connected", quiet.is_connected)
check("but silence is reported as silence",
      "no data yet" in quiet.traffic(), quiet.traffic())
quiet.tdx.sensor = _Fake((0.9, 0, 0), (0, 0, 0, 0))
quiet._poll()
check("a live frame drives the signal", quiet.tdx_frames == 1,
      quiet.tdx_frames)
check("and is reported", "via 3DxWare" in quiet.traffic(), quiet.traffic())

print("letting go stops the model, rather than coasting")
check("the puck's motion is held", any(quiet._state), quiet._state)
quiet.tdx.sensor = _Fake((0, 0, 0), (0, 0, 0, 0))      # hand off the puck
moves = []
quiet.moved.connect(lambda *v: moves.append(v))
quiet._poll()
check("the very next poll lets go", not any(quiet._state), quiet._state)
check("and nothing more is sent", not moves, moves)
quiet.tdx.sensor = None

settings = SpaceMouseSettings()
mouse = SpaceMouse(settings)

# feed synthetic HID reports and confirm they decode
import struct

mouse._consume([1] + list(struct.pack("<3h", 350, 0, 0)))
check("translation report decodes", abs(mouse._state[0] - 1.0) < 1e-6,
      mouse._state)
mouse._consume([2] + list(struct.pack("<3h", 0, -350, 0)))
check("rotation report decodes", abs(mouse._state[4] + 1.0) < 1e-6,
      mouse._state)
mouse._consume([1] + list(struct.pack("<6h", 175, 0, 0, 0, 0, 350)))
check("combined six-axis report decodes",
      abs(mouse._state[0] - 0.5) < 1e-6 and abs(mouse._state[5] - 1.0) < 1e-6,
      mouse._state)

mouse._state = [0.02, 0, 0, 0, 0, 0]
check("dead zone suppresses tiny motion", mouse._shaped()[0] == 0.0,
      mouse._shaped())

mouse._state = [1.0, 0, 0, 0, 0, 0]
check("full deflection reaches full scale",
      abs(mouse._shaped()[0] - 1.0) < 1e-6, mouse._shaped())

settings.invert["tx"] = True
check("axis inversion applies", mouse._shaped()[0] < 0, mouse._shaped())
settings.invert["tx"] = False

settings.dominant_axis = True
mouse._state = [0.9, 0.5, 0.3, 0, 0, 0]
shaped = mouse._shaped()
check("dominant axis mode keeps only the strongest",
      shaped[0] != 0 and shaped[1] == 0 and shaped[2] == 0, shaped)
settings.dominant_axis = False

buttons = []
mouse.button_pressed.connect(buttons.append)
mouse._consume([3, 0b101, 0, 0, 0])
check("buttons decode", buttons == [0, 2], buttons)

print("SpaceMouse drives the camera")
win.new_document(prompt=False)
win.new_primitive("box")
win._active_dialog.commit()
pump()
win.viewport.set_view("iso")
pump()


def camera_state():
    cam = win.viewport.view.Camera()
    eye = cam.Eye()
    return (round(eye.X(), 4), round(eye.Y(), 4), round(eye.Z(), 4),
            round(cam.Scale(), 4))


before_state = camera_state()
win.viewport.apply_spacemouse(0, 0, 0, 0.5, 0, 0)
pump()
check("orbit moved the camera", camera_state() != before_state,
      camera_state())

before_state = camera_state()
win.viewport.apply_spacemouse(0, 0.5, 0, 0, 0, 0)
pump()
check("push/pull zoomed", camera_state()[3] != before_state[3],
      "%s -> %s" % (before_state, camera_state()))

before_state = camera_state()
win.viewport.apply_spacemouse(0.5, 0, 0.5, 0, 0, 0)
pump()
check("slide panned", camera_state() != before_state, camera_state())

before_state = camera_state()
win.viewport.apply_spacemouse(0, 0, 0, 0, 0, 0)
check("zero motion leaves the camera alone",
      camera_state() == before_state)

print("sketch mode ignores the puck")
win.start_sketch_on_plane("XY")
pump()
win.viewport.finish_animation()   # the swing to the plane is animated now
pump()
before_state = camera_state()
win._spacemouse_moved(0, 0, 0, 0.8, 0, 0)
pump()
check("no orbit while sketching", camera_state() == before_state,
      camera_state())
win.finish_sketch()
pump()

print("settings dialog")
dialog = SpaceMouseDialog(win.spacemouse, win)
dialog.sliders["sensitivity"].setValue(200)
check("slider writes through to the settings",
      abs(win.spacemouse.settings.sensitivity - 2.0) < 1e-9,
      win.spacemouse.settings.sensitivity)
dialog.inverts["rz"].setChecked(True)
check("invert checkbox writes through",
      win.spacemouse.settings.invert["rz"] is True)
dialog._reset()
check("reset restores defaults",
      abs(win.spacemouse.settings.sensitivity - 1.0) < 1e-9
      and win.spacemouse.settings.invert["rz"] is False,
      win.spacemouse.settings.to_dict())
dialog._show_motion(0.1, 0.2, 0.3, 0.4, 0.5, 0.6)
check("live readout renders", "Tx +0.10" in dialog.live.text(),
      dialog.live.text())
dialog.close()

print()
if FAILS:
    print("%d FAILURES: %s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("all tree/input tests passed")
sys.exit(0)
