"""Preferences: the user's own name and colours.

The line this draws is the one worth testing.  A name belongs to the
document - a part that says who drew it is stating a fact about the part,
and opening it somewhere else does not change who that was.  A background
colour belongs to the person, and must never end up inside a file.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WORK = tempfile.mkdtemp(prefix="datum_prefs_")
os.environ["DATUM_CONFIG_DIR"] = WORK
os.environ["DATUM_SETTINGS_ORG"] = "IITEG-prefs-tests"

import harness  # noqa: E402,F401

from PySide6 import QtWidgets                                  # noqa: E402

from datum.core import prefs                                   # noqa: E402
from datum.core.assembly import AssemblyDocument               # noqa: E402
from datum.core.document import Document                       # noqa: E402
from datum.core.drawing import DrawingDocument                 # noqa: E402
from datum.core.views import model_properties                  # noqa: E402
from datum.ui import theme                                     # noqa: E402
from datum.ui.prefs_ui import PreferencesDialog                # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


print("where they live")

check("the config folder can be pointed somewhere safe",
      prefs.config_dir() == WORK, prefs.config_dir())
check("and there is nothing there to begin with",
      not os.path.exists(prefs.config_path()))
check("which is not an error", prefs.load().name == "")


print()
print("what they remember")

kept = prefs.Preferences(name="BIG BOSS", initials="BB", company="IITEG",
                         colours={"bg_top": "#204080"})
path = kept.save()
back = prefs.load()
check("the name comes back", back.name == "BIG BOSS", back.name)
check("so do the initials and the company",
      (back.initials, back.company) == ("BB", "IITEG"), back.to_dict())
check("and the colours", back.colours == {"bg_top": "#204080"}, back.colours)

junk = prefs.Preferences.from_dict(
    {"name": "x", "colours": {"bg_top": "not a colour", "window": "#ffffff"}})
check("a colour that is not one is dropped", "bg_top" not in junk.colours,
      junk.colours)
check("and so is a key the Display page does not offer",
      "window" not in junk.colours, junk.colours)

with open(path, "w", encoding="utf-8") as handle:
    handle.write("{ this is not json")
check("a corrupt file is not worth refusing to start over",
      prefs.load().name == "")
kept.save()


print()
print("who drew it")

prefs.reset()
mine = prefs.prefs()
check("the application picks them up", mine.name == "BIG BOSS", mine.name)

part = Document()
check("a new part is stamped", part.properties.get("Designer") == "BIG BOSS",
      part.properties)
check("and so is a new assembly",
      AssemblyDocument().properties.get("Designer") == "BIG BOSS")
check("a drawing calls the same person the author",
      DrawingDocument().properties.get("Author") == "BIG BOSS")

theirs = {"Designer": "Somebody Else"}
mine.stamp(theirs)
check("somebody else's part keeps its own designer",
      theirs["Designer"] == "Somebody Else", theirs)

part.properties["PartNumber"] = "NAPE-0001"
part.properties["Title"] = "Rear Hub"
opened = Document()
opened.load_dict(part.to_dict())
check("properties survive a round trip",
      opened.properties.get("PartNumber") == "NAPE-0001", opened.properties)

shown = model_properties("C:/somewhere/Rear Hub Body.pdat", None, opened)
check("a title block asks the part, not the file name",
      shown["Model.PartNumber"] == "NAPE-0001", shown["Model.PartNumber"])
check("and gets the title when there is one",
      shown["Model.Name"] == "Rear Hub", shown["Model.Name"])

plain = Document()
plain.properties = {}
bare = model_properties("C:/somewhere/Rear Hub Body.pdat", None, plain)
check("with nothing typed in, the file name still answers",
      bare["Model.PartNumber"] == "Rear Hub Body", bare["Model.PartNumber"])


print()
print("how it looks")

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

theme.apply_colours({"sketch_free": "#ff0000", "bg_top": "#204080"})
check("a string colour is set straight", theme.C.sketch_free == "#ff0000",
      theme.C.sketch_free)
check("a background colour becomes what the viewport wants",
      isinstance(theme.C.bg_top, tuple)
      and abs(theme.C.bg_top[2] - 128 / 255.0) < 0.01, theme.C.bg_top)
check("and reads back as the same hex",
      theme.as_hex(theme.C.bg_top) == "#204080", theme.as_hex(theme.C.bg_top))

theme.apply_colours({})
check("taking them off restores what DATUM ships with",
      theme.C.sketch_free == theme.shipped("sketch_free")
      and theme.C.bg_top == theme.shipped("bg_top"), theme.C.bg_top)

dialog = PreferencesDialog()
dialog.name_edit.setText("Someone New")
dialog.colour_buttons["sketch_free"].set_colour("#00ff00")
dialog.apply()
check("the dialog writes the name through",
      prefs.prefs().name == "Someone New", prefs.prefs().name)
check("and the colour is live at once", theme.C.sketch_free == "#00ff00",
      theme.C.sketch_free)
check("only what differs from the palette is stored",
      set(prefs.prefs().colours) == {"sketch_free"}, prefs.prefs().colours)

dialog.reset_colours()
check("reset puts the palette back",
      theme.C.sketch_free == theme.shipped("sketch_free"), theme.C.sketch_free)
check("and stores nothing at all", prefs.prefs().colours == {},
      prefs.prefs().colours)
dialog.deleteLater()

stored = Document()
stored.properties = {}
check("no colour ever reaches a document",
      "colours" not in stored.to_dict()
      and "bg_top" not in str(stored.to_dict()))


print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
