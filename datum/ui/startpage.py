"""The start page: what DATUM shows before a document is open.

Modelled on Inventor's home screen - a New/Open rail on the left, a list of
recent documents on the right, each with the thumbnail stored inside its own
archive.
"""

from __future__ import annotations

import os
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from .. import APP_NAME, __version__
from ..core import fileformat
from . import icons
from . import projects as projects_ui
from .theme import C, WORDMARK_STACK

class NewDocumentButton(QtWidgets.QToolButton):
    """One of the three big New tiles."""

    def __init__(self, icon_name: str, title: str, subtitle: str,
                 enabled: bool = True, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("StartTile")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFixedSize(168, 132)
        self.setEnabled(enabled)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 14, 10, 10)
        layout.setSpacing(4)

        badge = QtWidgets.QLabel()
        badge.setPixmap(icons.pixmap(icon_name, 46, self.devicePixelRatioF()))
        badge.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(badge)

        caption = QtWidgets.QLabel(title)
        caption.setAlignment(QtCore.Qt.AlignCenter)
        caption.setStyleSheet("font-size: 13px; font-weight: 600; color: %s;"
                              % (C.text if enabled else C.text_dim))
        layout.addWidget(caption)

        note = QtWidgets.QLabel(subtitle)
        note.setAlignment(QtCore.Qt.AlignCenter)
        note.setWordWrap(True)
        note.setStyleSheet("font-size: 10px; color: %s;" % C.text_dim)
        layout.addWidget(note)


class StartPage(QtWidgets.QWidget):
    """Shown in place of the model view until a document is open."""

    new_requested = QtCore.Signal(str)      # document type
    open_requested = QtCore.Signal()
    open_path_requested = QtCore.Signal(str)
    project_changed = QtCore.Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("StartPage")
        # the recent list belongs to the project, so switching project
        # switches which files the home page offers
        self.projects = projects_ui.manager()

        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_rail())
        root.addWidget(self._build_body(), 1)

        self.setStyleSheet("""
            #StartPage { background: %s; }
            #StartRail { background: %s; border-right: 1px solid %s; }
            /* every QWidget is given the window colour, and a label takes
               it literally - which paints a box behind the text a shade off
               whatever it is sitting on.  Labels have no business having a
               background of their own. */
            #StartRail QLabel { background: transparent; }
            #StartTile {
                background: %s;
                border: 1px solid %s;
                border-radius: 4px;
            }
            #StartTile:hover { border-color: %s; background: %s; }
            #StartTile:disabled { background: %s; }
        """ % (C.window, C.panel, C.border, C.panel_alt, C.border,
               C.accent, C.border, C.panel))

        self.refresh()

    # ------------------------------------------------------------ building

    def _build_rail(self) -> QtWidgets.QWidget:
        rail = QtWidgets.QWidget()
        rail.setObjectName("StartRail")
        rail.setFixedWidth(228)

        layout = QtWidgets.QVBoxLayout(rail)
        layout.setContentsMargins(22, 28, 22, 22)
        layout.setSpacing(10)

        # the mark and the wordmark sit together, the way they do on the
        # application icon, so the two read as one thing
        lockup = QtWidgets.QHBoxLayout()
        lockup.setSpacing(10)
        lockup.setContentsMargins(0, 0, 0, 0)

        # No tile behind it here.  The icon needs one to sit on a taskbar; a
        # lockup on a panel does not, and the tile is the darker square that
        # shows up round the mark.  The wordmark takes the mark's own colour
        # so the two never drift apart.
        mark = QtWidgets.QLabel()
        mark.setPixmap(icons.mark_pixmap(34, field=False,
                                         ratio=self.devicePixelRatioF()))
        mark.setFixedSize(34, 34)
        lockup.addWidget(mark)

        title = QtWidgets.QLabel(APP_NAME)
        title.setStyleSheet(
            "font-family: %s; font-size: 25px; font-weight: 800;"
            " letter-spacing: 4px; color: %s;"
            % (WORDMARK_STACK, icons.MARK_FACE))
        lockup.addWidget(title)
        lockup.addStretch(1)
        layout.addLayout(lockup)

        # which project the work goes into, right under the name, because
        # it decides where every save lands
        project_row = QtWidgets.QHBoxLayout()
        project_row.setSpacing(6)
        caption = QtWidgets.QLabel("Project")
        caption.setProperty("hint", True)
        project_row.addWidget(caption)
        self.project_button = QtWidgets.QToolButton()
        self.project_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.project_button.setStyleSheet(
            "QToolButton { border: none; background: transparent; padding: 0;"
            " color: %s; font-size: 12px; font-weight: 600;"
            " text-decoration: underline; }"
            "QToolButton:hover { color: %s; }" % (C.accent, C.ok))
        self.project_button.clicked.connect(self.choose_project)
        project_row.addWidget(self.project_button)
        project_row.addStretch(1)

        version = QtWidgets.QLabel("Version %s" % __version__)
        version.setStyleSheet("color: %s; font-size: 11px;" % C.text_dim)
        layout.addWidget(version)
        layout.addLayout(project_row)

        layout.addSpacing(26)

        open_button = QtWidgets.QPushButton("Open...")
        open_button.setMinimumHeight(34)
        open_button.clicked.connect(self.open_requested.emit)
        layout.addWidget(open_button)

        new_button = QtWidgets.QPushButton("New Part")
        new_button.setProperty("primary", True)
        new_button.setMinimumHeight(34)
        new_button.clicked.connect(
            lambda: self.new_requested.emit(fileformat.PART))
        layout.addWidget(new_button)

        assembly_button = QtWidgets.QPushButton("New Assembly")
        assembly_button.setMinimumHeight(34)
        assembly_button.clicked.connect(
            lambda: self.new_requested.emit(fileformat.ASSEMBLY))
        layout.addWidget(assembly_button)

        cam_button = QtWidgets.QPushButton("New CAM Sheet")
        cam_button.setMinimumHeight(34)
        cam_button.clicked.connect(
            lambda: self.new_requested.emit(fileformat.CAM))
        layout.addWidget(cam_button)

        layout.addStretch(1)

        note = QtWidgets.QLabel(
            "Parts, assemblies, CAM sheets and drawings are all "
            "editable in this build.")
        note.setWordWrap(True)
        note.setStyleSheet("color: %s; font-size: 10px;" % C.text_dim)
        layout.addWidget(note)

        return rail

    def _build_body(self) -> QtWidgets.QWidget:
        body = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(body)
        layout.setContentsMargins(34, 30, 34, 26)
        layout.setSpacing(14)

        heading = QtWidgets.QLabel("New")
        heading.setStyleSheet("font-size: 19px; font-weight: 600;")
        layout.addWidget(heading)

        tiles = QtWidgets.QHBoxLayout()
        tiles.setSpacing(14)

        self.part_tile = NewDocumentButton(
            "box", "Part", "A single solid body  .pdat")
        self.part_tile.clicked.connect(
            lambda: self.new_requested.emit(fileformat.PART))
        tiles.addWidget(self.part_tile)

        self.assembly_tile = NewDocumentButton(
            "pattern", "Assembly", "Components placed together  .adat")
        self.assembly_tile.setToolTip(
            "Place parts and constrain them to each other")
        self.assembly_tile.clicked.connect(
            lambda: self.new_requested.emit(fileformat.ASSEMBLY))
        tiles.addWidget(self.assembly_tile)

        self.cam_tile = NewDocumentButton(
            "export", "CAM Sheet", "Flat parts nested to cut  .cdat")
        self.cam_tile.setToolTip(
            "Lay flat parts on stock, offset for the cutter, export DXF")
        self.cam_tile.clicked.connect(
            lambda: self.new_requested.emit(fileformat.CAM))
        tiles.addWidget(self.cam_tile)

        self.drawing_tile = NewDocumentButton(
            "dimension", "Drawing", "Sheets of views  .ddat")
        self.drawing_tile.setToolTip(
            "Views generated from a part or an assembly, on a sheet")
        self.drawing_tile.clicked.connect(
            lambda: self.new_requested.emit(fileformat.DRAWING))
        tiles.addWidget(self.drawing_tile)

        tiles.addStretch(1)
        layout.addLayout(tiles)

        layout.addSpacing(12)

        header = QtWidgets.QHBoxLayout()
        recent_label = QtWidgets.QLabel("Recent")
        recent_label.setStyleSheet("font-size: 19px; font-weight: 600;")
        header.addWidget(recent_label)
        header.addStretch(1)
        self.clear_button = QtWidgets.QPushButton("Clear")
        self.clear_button.clicked.connect(self._clear_recent)
        header.addWidget(self.clear_button)
        layout.addLayout(header)

        self.recent_list = QtWidgets.QListWidget()
        self.recent_list.setIconSize(QtCore.QSize(56, 56))
        self.recent_list.setAlternatingRowColors(True)
        self.recent_list.setStyleSheet(
            "QListWidget::item { padding: 6px; }")
        self.recent_list.itemActivated.connect(self._activate)
        self.recent_list.itemClicked.connect(self._activate)
        layout.addWidget(self.recent_list, 1)

        self.empty_note = QtWidgets.QLabel(
            "Nothing opened yet. Start a new part, or open an existing one.")
        self.empty_note.setStyleSheet("color: %s;" % C.text_dim)
        layout.addWidget(self.empty_note)

        return body

    # ----------------------------------------------------------- behaviour

    def refresh(self) -> None:
        self.recent_list.clear()
        project = self.projects.active()
        self.project_button.setText(project.name)
        self.project_button.setToolTip(
            "Project: %s\nDocuments go in %s\n\nClick to switch project."
            % (project.name, project.folder))
        entries = list(project.recent)

        for entry in entries:
            path = entry["path"]
            exists = os.path.exists(path)
            item = QtWidgets.QListWidgetItem()
            item.setData(QtCore.Qt.UserRole, path)

            name = os.path.basename(path)
            folder = os.path.dirname(path)
            item.setText("%s\n%s" % (name, folder))
            item.setIcon(self._icon_for(path, entry.get("type")))
            if not exists:
                item.setText("%s  (missing)\n%s" % (name, folder))
                item.setForeground(QtGui.QBrush(QtGui.QColor(C.text_dim)))
            self.recent_list.addItem(item)

        self.recent_list.setVisible(bool(entries))
        self.empty_note.setVisible(not entries)
        self.clear_button.setVisible(bool(entries))

    def _icon_for(self, path: str, doc_type: Optional[str]) -> QtGui.QIcon:
        """Prefer the preview stored in the archive over a generic icon."""
        data = fileformat.thumbnail_of(path) if os.path.exists(path) else None
        if data:
            pixmap = QtGui.QPixmap()
            if pixmap.loadFromData(data):
                return QtGui.QIcon(pixmap)
        fallback = {fileformat.ASSEMBLY: "pattern",
                    fileformat.CAM: "export",
                    fileformat.DRAWING: "dimension"}.get(doc_type or "", "box")
        return icons.icon(fallback, 40)

    def _activate(self, item: QtWidgets.QListWidgetItem) -> None:
        path = item.data(QtCore.Qt.UserRole)
        if path:
            self.open_path_requested.emit(path)

    def _clear_recent(self) -> None:
        self.projects.active().clear_recent()
        self.refresh()

    def remember(self, path: str, doc_type: str = fileformat.PART) -> None:
        self.projects.active().remember(path, doc_type)
        self.refresh()

    def choose_project(self) -> None:
        dialog = projects_ui.ProjectsDialog(self.projects, self)
        dialog.activated.connect(lambda _p: self.refresh())
        dialog.exec()
        self.refresh()
        self.project_changed.emit()
