"""Projects: which folder the work of the moment lives in.

Inventor's idea, and a good one.  A project is a file sitting in a folder,
and that folder is where documents are saved and looked for.  Switching
project switches the place everything happens, and the recent list with it,
so two jobs never leave their files interleaved in one directory.

The project file is plain JSON rather than a DATUM archive.  It holds no
geometry, people do read it when something has gone wrong, and it wants to
survive being copied around with the folder it describes.

What is *in* a project - its name, its recent documents - lives in the file.
What is merely true of this machine - which projects are known about, which
one is active - is settings, and is kept by the caller.  That split is what
lets a project folder be copied to another machine and still make sense.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import fileformat

EXTENSION = ".dproj"
MAX_RECENT = 12
DEFAULT_NAME = "Default"
DEFAULT_FOLDER = "DATUM"


class ProjectError(Exception):
    """A project could not be read, written or created."""


@dataclass
class Project:
    """One project file, and the folder it speaks for."""

    name: str = DEFAULT_NAME
    path: str = ""
    recent: List[Dict[str, Any]] = field(default_factory=list)
    created: str = ""

    @property
    def folder(self) -> str:
        """Where this project's documents live: the file's own directory."""
        return os.path.dirname(os.path.abspath(self.path)) if self.path else ""

    @property
    def valid(self) -> bool:
        return bool(self.path) and os.path.isfile(self.path)

    # ------------------------------------------------------------- recents

    def remember(self, path: str, doc_type: str = fileformat.PART) -> None:
        path = os.path.abspath(path)
        self.recent = [e for e in self.recent
                       if os.path.normcase(e.get("path", ""))
                       != os.path.normcase(path)]
        self.recent.insert(0, {"path": path, "type": doc_type,
                               "opened": fileformat.now()})
        del self.recent[MAX_RECENT:]
        self.save()

    def forget(self, path: str) -> None:
        self.recent = [e for e in self.recent
                       if os.path.normcase(e.get("path", ""))
                       != os.path.normcase(path)]
        self.save()

    def clear_recent(self) -> None:
        self.recent = []
        self.save()

    # ------------------------------------------------------- the file itself

    def to_dict(self) -> Dict[str, Any]:
        return {"datum_project": 1, "name": self.name,
                "created": self.created, "recent": self.recent}

    def save(self) -> str:
        if not self.path:
            raise ProjectError("this project has nowhere to be saved")
        folder = os.path.dirname(os.path.abspath(self.path))
        try:
            os.makedirs(folder, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump(self.to_dict(), handle, indent=2)
        except OSError as exc:
            raise ProjectError("could not write %s: %s" % (self.path, exc))
        return self.path

    @classmethod
    def load(cls, path: str) -> "Project":
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectError("could not read %s: %s"
                               % (os.path.basename(path), exc))
        if not isinstance(data, dict):
            raise ProjectError("%s is not a project file"
                               % os.path.basename(path))
        recent = [e for e in data.get("recent", [])
                  if isinstance(e, dict) and e.get("path")]
        return cls(name=str(data.get("name")
                            or os.path.splitext(os.path.basename(path))[0]),
                   path=os.path.abspath(path),
                   recent=recent,
                   created=str(data.get("created") or fileformat.now()))

    @classmethod
    def create(cls, name: str, folder: str) -> "Project":
        """Start a project in ``folder``, refusing to sit on an existing one."""
        name = (name or "").strip()
        if not name:
            raise ProjectError("a project needs a name")
        if any(ch in name for ch in '\\/:*?"<>|'):
            raise ProjectError("a project name cannot contain \\ / : * ? \" < > |")
        path = os.path.join(os.path.abspath(folder), name + EXTENSION)
        if os.path.exists(path):
            raise ProjectError("%s already has a project called %s"
                               % (folder, name))
        project = cls(name=name, path=path, created=fileformat.now())
        project.save()
        return project


def documents_folder(home: Optional[str] = None) -> str:
    """Where a fresh install puts its first project."""
    base = home or os.path.join(os.path.expanduser("~"), "Documents")
    return os.path.join(base, DEFAULT_FOLDER)


class ProjectManager:
    """The known projects, and which one is in use.

    The store is handed in rather than reached for, so the UI can keep the
    list in Qt's settings and a test can keep it in a dictionary.  It needs
    only ``get(key, default)`` and ``set(key, value)``.
    """

    ACTIVE = "project/active"
    KNOWN = "project/known"

    def __init__(self, store, home: Optional[str] = None) -> None:
        self.store = store
        self.home = home
        self._cache: Dict[str, Project] = {}

    # ------------------------------------------------------------- the list

    def known_paths(self) -> List[str]:
        raw = self.store.get(self.KNOWN, "[]")
        try:
            paths = json.loads(raw) if isinstance(raw, str) else list(raw)
        except (json.JSONDecodeError, TypeError):
            paths = []
        return [p for p in paths if isinstance(p, str)]

    def _set_known(self, paths: List[str]) -> None:
        seen, out = set(), []
        for path in paths:
            key = os.path.normcase(os.path.abspath(path))
            if key not in seen:
                seen.add(key)
                out.append(os.path.abspath(path))
        self.store.set(self.KNOWN, json.dumps(out))

    def known(self) -> List[Project]:
        """Every project still on disk, the missing ones quietly dropped."""
        out, alive = [], []
        for path in self.known_paths():
            if not os.path.isfile(path):
                continue
            try:
                out.append(Project.load(path))
                alive.append(path)
            except ProjectError:
                continue
        if alive != self.known_paths():
            self._set_known(alive)
        return out

    def add(self, project: Project) -> None:
        self._set_known([project.path] + self.known_paths())

    def forget(self, path: str) -> None:
        """Take a project off the list without touching the folder itself."""
        key = os.path.normcase(os.path.abspath(path))
        self._set_known([p for p in self.known_paths()
                         if os.path.normcase(os.path.abspath(p)) != key])
        self._cache.pop(key, None)
        if os.path.normcase(self.active_path()) == key:
            rest = self.known()
            self.store.set(self.ACTIVE, rest[0].path if rest else "")

    # ---------------------------------------------------------- the active one

    def active_path(self) -> str:
        return str(self.store.get(self.ACTIVE, "") or "")

    def activate(self, path: str) -> Project:
        project = Project.load(path)
        self.store.set(self.ACTIVE, project.path)
        self.add(project)
        self._cache[os.path.normcase(project.path)] = project
        return project

    def active(self) -> Project:
        """The project in use, making the default one if there is none yet."""
        path = self.active_path()
        key = os.path.normcase(path)
        if path and os.path.isfile(path):
            if key in self._cache:
                return self._cache[key]
            try:
                project = Project.load(path)
                self._cache[key] = project
                return project
            except ProjectError:
                pass
        return self.ensure_default()

    def ensure_default(self) -> Project:
        """The project a fresh install starts in: Documents, ready to use."""
        folder = documents_folder(self.home)
        path = os.path.join(folder, DEFAULT_NAME + EXTENSION)
        if os.path.isfile(path):
            project = Project.load(path)
        else:
            project = Project(name=DEFAULT_NAME, path=path,
                              created=fileformat.now())
            project.save()
        self.store.set(self.ACTIVE, project.path)
        self.add(project)
        self._cache[os.path.normcase(project.path)] = project
        return project

    def create(self, name: str, folder: str, activate: bool = True) -> Project:
        project = Project.create(name, folder)
        self.add(project)
        if activate:
            self.store.set(self.ACTIVE, project.path)
            self._cache[os.path.normcase(project.path)] = project
        return project


class DictStore:
    """A settings store backed by a plain dictionary, for tests."""

    def __init__(self, data: Optional[Dict[str, Any]] = None) -> None:
        self.data = dict(data or {})

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value
