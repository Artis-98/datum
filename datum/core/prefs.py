"""Who is using this, and how they want it to look.

Two unrelated-looking things live in one file for one reason: both belong
to the person, not to the document.  A part carries the name of whoever
designed it because that is a fact about the part; the colour of the
background is a fact about the person looking at it, and it has no
business being saved into a file somebody else will open.

So this is the user's own corner: a small JSON file next to their own
materials, outside the installation, which an update replaces wholesale
and must never touch.

It deliberately holds no credentials.  Nothing here talks to a server, and
a store of secrets with nothing to unlock is a liability rather than a
feature.  If something ever does need one, it goes in the operating
system's keyring, not in a plain file beside the preferences.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

APP_FOLDER = "DATUM"
ORG_FOLDER = "IITEG"
FILE_NAME = "preferences.json"

# the appearance keys the Display page is allowed to set, which is a short
# list on purpose: these are the ones somebody actually wants to change
COLOUR_KEYS = (
    "bg_top", "bg_bottom",
    "sketch_line", "sketch_free", "sketch_construction",
)


def config_dir() -> str:
    """Where this machine keeps the user's own DATUM files.

    Honours ``DATUM_CONFIG_DIR`` so a test run cannot disturb the
    preferences of whoever is actually working in the application.
    """
    forced = os.environ.get("DATUM_CONFIG_DIR")
    if forced:
        return forced
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME")
    if base:
        return os.path.join(base, ORG_FOLDER, APP_FOLDER)
    return os.path.join(os.path.expanduser("~"), ".config", APP_FOLDER.lower())


def config_path(name: str = FILE_NAME) -> str:
    return os.path.join(config_dir(), name)


@dataclass
class Preferences:
    """Everything the application remembers about its user."""

    # who they are.  Stamped onto new documents, and read by a title block.
    name: str = ""
    initials: str = ""
    company: str = ""
    # what they want it to look like: theme colour name -> "#rrggbb"
    colours: Dict[str, str] = field(default_factory=dict)
    # folders whose documents may run their dLogic without asking.  Kept
    # here, on this machine, because a file must never vouch for itself.
    trusted_folders: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "initials": self.initials,
                "company": self.company, "colours": dict(self.colours),
                "trusted_folders": list(self.trusted_folders)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Preferences":
        colours = data.get("colours") or {}
        return cls(
            name=str(data.get("name", "")),
            initials=str(data.get("initials", "")),
            company=str(data.get("company", "")),
            colours={str(k): str(v) for k, v in colours.items()
                     if k in COLOUR_KEYS and _is_colour(v)},
            trusted_folders=[str(f) for f in
                             data.get("trusted_folders", []) or [] if f])

    def stamp(self, properties: Dict[str, str],
              key: str = "Designer") -> Dict[str, str]:
        """Fill in the fields a new document should already know.

        Only the blank ones: a document that says who drew it is stating a
        fact, and opening it on somebody else's machine does not change
        who that was.
        """
        if self.name and not properties.get(key):
            properties[key] = self.name
        if self.company and not properties.get("Company"):
            properties["Company"] = self.company
        return properties

    def save(self, path: str = "") -> str:
        target = path or config_path()
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=2)
            handle.write("\n")
        return target


def _is_colour(value: Any) -> bool:
    text = str(value)
    return (len(text) == 7 and text.startswith("#")
            and all(c in "0123456789abcdefABCDEF" for c in text[1:]))


def load(path: str = "") -> Preferences:
    """Read the file, or hand back the defaults if there is not one yet."""
    target = path or config_path()
    if not os.path.exists(target):
        return Preferences()
    try:
        with open(target, encoding="utf-8") as handle:
            return Preferences.from_dict(json.load(handle))
    except (OSError, ValueError):
        # a corrupt preferences file is not worth refusing to start over
        return Preferences()


_PREFS: Optional[Preferences] = None


def prefs() -> Preferences:
    """The one set of preferences the application is running with."""
    global _PREFS
    if _PREFS is None:
        _PREFS = load()
    return _PREFS


def reset() -> None:
    """Forget them, so the next ask re-reads the file.  For tests, and for
    the moment the Preferences window writes a new one."""
    global _PREFS
    _PREFS = None
