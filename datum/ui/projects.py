"""Choosing which project the work goes into.

The core knows what a project is; this is the window that lists them, makes
one, and says which is in use.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from .. import APP_NAME
from ..core import project as core
from . import icons
from .theme import C


class SettingsStore:
    """The project list, kept where the rest of this app's settings are."""

    def __init__(self) -> None:
        # the organisation can be overridden so a test run does not disturb
        # the projects somebody is actually working in
        self._settings = QtCore.QSettings(
            os.environ.get("DATUM_SETTINGS_ORG", "IITEG"), APP_NAME)

    def get(self, key: str, default: Any = None) -> Any:
        return self._settings.value(key, default)

    def set(self, key: str, value: Any) -> None:
        self._settings.setValue(key, value)


def manager() -> core.ProjectManager:
    """A manager pointed at this machine's real Documents folder."""
    # DATUM_DOCUMENTS stands in for Documents, so a test run keeps its
    # projects, and the recent files in them, out of the real one
    documents = os.environ.get("DATUM_DOCUMENTS") or         QtCore.QStandardPaths.writableLocation(
            QtCore.QStandardPaths.DocumentsLocation)
    return core.ProjectManager(SettingsStore(), home=documents or None)


class ProjectsDialog(QtWidgets.QDialog):
    """Inventor's Projects window: the list, and which one is in use."""

    activated = QtCore.Signal(str)

    def __init__(self, projects: core.ProjectManager, parent=None) -> None:
        super().__init__(parent)
        self.projects = projects
        self.setWindowTitle("Projects")
        self.setWindowIcon(icons.icon("open", 24))
        self.setMinimumSize(560, 380)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(9)

        note = QtWidgets.QLabel(
            "The active project decides where documents are saved and which "
            "recent files the home page shows. Its file sits in the folder "
            "it speaks for, so the whole folder can be moved or copied.")
        note.setWordWrap(True)
        note.setProperty("hint", True)
        root.addWidget(note)

        self.list = QtWidgets.QListWidget()
        self.list.setAlternatingRowColors(True)
        self.list.itemActivated.connect(lambda _i: self.make_active())
        self.list.currentItemChanged.connect(lambda *_: self._sync())
        root.addWidget(self.list, 1)

        row = QtWidgets.QHBoxLayout()
        self.new_button = QtWidgets.QPushButton("New Project...")
        self.new_button.clicked.connect(self.new_project)
        row.addWidget(self.new_button)

        self.browse_button = QtWidgets.QPushButton("Add Existing...")
        self.browse_button.clicked.connect(self.browse)
        row.addWidget(self.browse_button)

        self.remove_button = QtWidgets.QPushButton("Remove")
        self.remove_button.setToolTip(
            "Take it off this list. The folder and its files are left alone.")
        self.remove_button.clicked.connect(self.remove)
        row.addWidget(self.remove_button)
        row.addStretch(1)
        root.addLayout(row)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        self.active_button = QtWidgets.QPushButton("Make Active")
        self.active_button.setProperty("primary", True)
        self.active_button.clicked.connect(self.make_active)
        buttons.addWidget(self.active_button)
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        root.addLayout(buttons)

        self.refresh()

    # ---------------------------------------------------------------- state

    def refresh(self) -> None:
        active = os.path.normcase(self.projects.active_path())
        self.list.clear()
        for entry in self.projects.known():
            item = QtWidgets.QListWidgetItem()
            item.setData(QtCore.Qt.UserRole, entry.path)
            live = os.path.normcase(entry.path) == active
            item.setText("%s%s\n%s"
                         % (entry.name, "   (active)" if live else "",
                            entry.folder))
            item.setIcon(icons.icon("open", 28))
            if live:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                item.setForeground(QtGui.QBrush(QtGui.QColor(C.ok)))
            self.list.addItem(item)
            if live:
                # after adding, not before: an item the list does not own yet
                # cannot be made the current one
                self.list.setCurrentItem(item)
        self._sync()

    def _sync(self) -> None:
        path = self.selected()
        active = os.path.normcase(self.projects.active_path())
        chosen = bool(path)
        self.active_button.setEnabled(
            chosen and os.path.normcase(path or "") != active)
        # the active project always stays on the list: removing it would
        # leave the application with nowhere to save
        self.remove_button.setEnabled(
            chosen and os.path.normcase(path or "") != active)

    def selected(self) -> Optional[str]:
        item = self.list.currentItem()
        return item.data(QtCore.Qt.UserRole) if item else None

    # --------------------------------------------------------------- actions

    def make_active(self) -> None:
        path = self.selected()
        if not path:
            return
        try:
            self.projects.activate(path)
        except core.ProjectError as exc:
            QtWidgets.QMessageBox.warning(self, "Projects", str(exc))
            self.refresh()
            return
        self.refresh()
        self.activated.emit(path)

    def new_project(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Folder for the new project",
            self.projects.active().folder or "")
        if not folder:
            return
        suggested = os.path.basename(os.path.normpath(folder)) or "Project"
        name, ok = QtWidgets.QInputDialog.getText(
            self, "New Project", "Project name:",
            QtWidgets.QLineEdit.Normal, suggested)
        if not ok:
            return
        try:
            created = self.projects.create(name, folder)
        except core.ProjectError as exc:
            QtWidgets.QMessageBox.warning(self, "New Project", str(exc))
            return
        self.refresh()
        self.activated.emit(created.path)

    def browse(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Add an existing project", "",
            "DATUM project (*%s)" % core.EXTENSION)
        if not path:
            return
        try:
            self.projects.activate(path)
        except core.ProjectError as exc:
            QtWidgets.QMessageBox.warning(self, "Projects", str(exc))
            return
        self.refresh()
        self.activated.emit(path)

    def remove(self) -> None:
        path = self.selected()
        if not path:
            return
        answer = QtWidgets.QMessageBox.question(
            self, "Remove project",
            "Take %s off the list?\n\nThe folder and everything in it is "
            "left exactly as it is." % os.path.basename(path),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel)
        if answer != QtWidgets.QMessageBox.Yes:
            return
        self.projects.forget(path)
        self.refresh()
