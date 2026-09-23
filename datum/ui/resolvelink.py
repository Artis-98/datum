"""Pointing a document at a model that has moved.

Assemblies, CAM sheets and drawings all reference files by path, and all
three hit the same problem when somebody reorganises a folder.  Reporting
"cannot find Side Panel.pdat" and stopping is not much help; this lets the
reference be pointed at where the file actually is now, and offers to fix
the rest of the broken ones in the same folder while it is at it.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence

from PySide6 import QtCore, QtGui, QtWidgets

from ..core import fileformat
from ..core.fileformat import BrokenLink, ComponentRef
from . import icons
from .theme import C


class ResolveLinkDialog(QtWidgets.QDialog):
    """Repoint references whose files are no longer where they were."""

    def __init__(self, parent, broken: Sequence[BrokenLink], base_dir: str,
                 title: str = "Resolve Link") -> None:
        super().__init__(parent)
        self.broken = list(broken)
        self.base_dir = base_dir
        self.fixed: Dict[int, str] = {}      # index in broken -> new path
        self.setWindowTitle(title)
        self.setMinimumSize(600, 360)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(9)

        note = QtWidgets.QLabel(
            "These files could not be found where the document expects them. "
            "Point one at where it is now and anything else missing from the "
            "same folder is picked up with it.")
        note.setWordWrap(True)
        note.setProperty("hint", True)
        root.addWidget(note)

        self.list = QtWidgets.QListWidget()
        self.list.itemDoubleClicked.connect(lambda _i: self.browse())
        root.addWidget(self.list, 1)

        row = QtWidgets.QHBoxLayout()
        self.browse_button = QtWidgets.QPushButton("Find File...")
        self.browse_button.clicked.connect(self.browse)
        row.addWidget(self.browse_button)
        self.folder_button = QtWidgets.QPushButton("Search a Folder...")
        self.folder_button.setToolTip(
            "Look in one folder for everything that is missing")
        self.folder_button.clicked.connect(self.search_folder)
        row.addWidget(self.folder_button)
        row.addStretch(1)
        root.addLayout(row)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Apply")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.refresh()

    # ---------------------------------------------------------------- state

    def refresh(self) -> None:
        current = self.list.currentRow()
        self.list.clear()
        for index, link in enumerate(self.broken):
            found = self.fixed.get(index)
            name = link.reference.name or link.reference.path or "(unnamed)"
            if found:
                text = "%s\n    found: %s" % (name, found)
                colour = C.ok
            else:
                text = "%s\n    was: %s" % (name, link.reference.path)
                colour = C.error
            item = QtWidgets.QListWidgetItem(text)
            item.setForeground(QtGui.QBrush(QtGui.QColor(colour)))
            item.setIcon(icons.icon("box" if found else "delete", 18))
            self.list.addItem(item)
        self.list.setCurrentRow(max(0, min(current, self.list.count() - 1)))

    def selected(self) -> int:
        return self.list.currentRow()

    # -------------------------------------------------------------- actions

    def browse(self) -> None:
        index = self.selected()
        if index < 0:
            return
        link = self.broken[index]
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Find %s" % (link.reference.name or "the model"),
            self.base_dir,
            "Parts and assemblies (*.pdat *.adat);;All files (*)")
        if not path:
            return
        self.fixed[index] = path
        self._sweep(os.path.dirname(path))
        self.refresh()

    def search_folder(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Folder to search", self.base_dir)
        if folder:
            self._sweep(folder)
            self.refresh()

    def _sweep(self, folder: str) -> None:
        """Match anything still missing against the names in one folder."""
        if not folder or not os.path.isdir(folder):
            return
        try:
            present = {name.lower(): os.path.join(folder, name)
                       for name in os.listdir(folder)}
        except OSError:
            return
        for index, link in enumerate(self.broken):
            if index in self.fixed:
                continue
            wanted = (link.reference.name or "").lower()
            if wanted and wanted in present:
                self.fixed[index] = present[wanted]

    def apply(self) -> int:
        """Rewrite the references that were found.  Returns how many."""
        count = 0
        for index, path in self.fixed.items():
            reference = self.broken[index].reference
            reference.path = (fileformat.relative_path(path, self.base_dir)
                              if self.base_dir else path)
            reference.name = os.path.basename(path)
            count += 1
        return count


def resolve(parent, broken: Sequence[BrokenLink], base_dir: str,
            title: str = "Resolve Link") -> int:
    """Show the dialog and apply what was found.  Returns how many were fixed."""
    if not broken:
        return 0
    dialog = ResolveLinkDialog(parent, broken, base_dir, title)
    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return 0
    return dialog.apply()
