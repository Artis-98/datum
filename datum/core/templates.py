"""Drawing templates.

A template is not a special kind of file.  It is an ordinary ``.ddat``
sitting in a folder called Templates, and starting a drawing from one
copies it and forgets where it came from.  That is the whole mechanism, and
it means anything you can draw you can make a template of - no separate
editor, no second file format, and a template you can open and check.

Templates live in the active project's folder, so a project can carry its
own title block; the ones shipped with the application are the fallback
when a project has none of its own.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import List, Optional

from . import drawing as dwg
from . import fileformat

FOLDER = "Templates"


@dataclass
class Template:
    """One template file, and where it came from."""

    name: str
    path: str
    builtin: bool = False

    @property
    def label(self) -> str:
        return "%s%s" % (self.name, "   (shipped)" if self.builtin else "")


def builtin_folder() -> str:
    """Where the templates that ship with the application live."""
    return os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "templates")


def project_folder(project_dir: str) -> str:
    return os.path.join(project_dir, FOLDER) if project_dir else ""


def available(project_dir: str = "") -> List[Template]:
    """Every template on offer: the project's own first, then the shipped.

    A project template with the same name as a shipped one wins, so a
    company title block can quietly replace the default without anyone
    having to delete anything.
    """
    out: List[Template] = []
    seen = set()
    for folder, builtin in ((project_folder(project_dir), False),
                            (builtin_folder(), True)):
        if not folder or not os.path.isdir(folder):
            continue
        for entry in sorted(os.listdir(folder)):
            if not entry.lower().endswith(fileformat.EXTENSIONS[
                    fileformat.DRAWING]):
                continue
            name = os.path.splitext(entry)[0]
            if name.lower() in seen:
                continue
            seen.add(name.lower())
            out.append(Template(name=name,
                                path=os.path.join(folder, entry),
                                builtin=builtin))
    return out


def find(name: str, project_dir: str = "") -> Optional[Template]:
    for template in available(project_dir):
        if template.name.lower() == (name or "").lower():
            return template
    return None


def new_from(template: Optional[Template]) -> "dwg.DrawingDocument":
    """A fresh untitled drawing with the template's sheets and styles.

    Loaded and then cut loose: the new document keeps no link back, because
    a template is a starting point and not a thing to stay attached to.
    """
    if template is None or not os.path.isfile(template.path):
        document = dwg.DrawingDocument()
        document.add_sheet("A3")
        return document

    document = dwg.DrawingDocument.load(template.path)
    document.path = ""
    document.created = fileformat.now()
    document.modified = False
    document.thumbnail = None
    # a template may have been saved with views on it as an example; the
    # drawing made from it starts empty, keeping the sheets and their
    # furniture but none of somebody else's geometry
    for sheet in document.sheets:
        sheet.views = []
        sheet.annotations = []
    document.hashes = {}
    return document


def save_as_template(document: "dwg.DrawingDocument", name: str,
                     project_dir: str) -> str:
    """Write the drawing into the project's Templates folder."""
    name = (name or "").strip()
    if not name:
        raise ValueError("a template needs a name")
    if any(ch in name for ch in '\\/:*?"<>|'):
        raise ValueError('a template name cannot contain \\ / : * ? " < > |')
    folder = project_folder(project_dir)
    if not folder:
        raise ValueError("there is no project to save the template into")
    os.makedirs(folder, exist_ok=True)

    target = os.path.join(folder, name + fileformat.EXTENSIONS[
        fileformat.DRAWING])
    # saved through the document rather than copied, so a template written
    # from an open drawing carries whatever is on screen right now
    keep_path = document.path
    try:
        document.path = target
        written = document.save(target)
    finally:
        document.path = keep_path
    return written


def ensure_builtins() -> List[str]:
    """Write the shipped templates if they are not there yet.

    They are generated rather than stored as files because they are only a
    sheet size and a title block, and code that builds them cannot fall out
    of step with the code that reads them.
    """
    folder = builtin_folder()
    os.makedirs(folder, exist_ok=True)
    written: List[str] = []
    for name, size, standard in (("ISO A3", "A3", "ISO"),
                                 ("ISO A4", "A4", "ISO"),
                                 ("ISO A2", "A2", "ISO"),
                                 ("ANSI B", "Tabloid", "ANSI"),
                                 ("ANSI A", "Letter", "ANSI")):
        path = os.path.join(folder, name + fileformat.EXTENSIONS[
            fileformat.DRAWING])
        if os.path.isfile(path):
            continue
        document = dwg.DrawingDocument()
        document.standard = standard
        document.properties["Company"] = ""
        sheet = document.add_sheet(size)
        sheet.name = "Sheet1"
        document.path = path
        document.save(path)
        written.append(path)
    return written
