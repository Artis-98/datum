"""The open-document tab strip along the bottom of the window.

Inventor keeps every open file in one process and switches between them with
a row of tabs; SolidWorks opens a whole new application window per document.
The tab strip is the better idea and this is it, Home tab included.

The bar owns nothing but the tabs.  Which document each one stands for is a
key the window hands over, so closing and reordering never has to guess.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ..core import fileformat
from . import icons
from .theme import C

HOME = "__home__"

TYPE_ICONS = {
    fileformat.PART: "box",
    fileformat.ASSEMBLY: "pattern",
    fileformat.CAM: "export",
    fileformat.DRAWING: "dimension",
}


class DocumentTabs(QtWidgets.QTabBar):
    """One tab per open document, with Home pinned at the front."""

    document_selected = QtCore.Signal(str)      # key, or HOME
    close_requested = QtCore.Signal(str)
    save_requested = QtCore.Signal(str)
    close_others_requested = QtCore.Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("DocumentTabs")
        self.setExpanding(False)
        self.setDrawBase(False)
        self.setElideMode(QtCore.Qt.ElideMiddle)
        self.setUsesScrollButtons(True)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.setIconSize(QtCore.QSize(14, 14))
        self.setTabsClosable(True)
        self.setMovable(False)

        self.setStyleSheet("""
            #DocumentTabs { background: %s; border-top: 1px solid %s; }
            #DocumentTabs::tab {
                background: %s;
                color: %s;
                border: 1px solid %s;
                border-bottom: none;
                padding: 3px 10px 3px 8px;
                margin-right: 1px;
                max-width: 260px;
            }
            #DocumentTabs::tab:selected {
                background: %s;
                color: %s;
                border-top: 2px solid %s;
            }
            #DocumentTabs::tab:hover:!selected { background: %s; }
            #DocumentTabs QToolButton {
                border: none;
                background: transparent;
                margin-left: 4px;
            }
            #DocumentTabs QToolButton:hover { background: %s; }
        """ % (C.ribbon_tab, C.border, C.ribbon_tab, C.text_dim, C.border,
               C.panel_alt, C.text, C.accent, C.panel, C.border_light))

        self._keys: List[str] = []
        self._updating = False

        self.currentChanged.connect(self._changed)
        self.tabCloseRequested.connect(self._close_clicked)
        self.customContextMenuRequested.connect(self._menu)

        self.reset([])

    # ------------------------------------------------------------- building

    def reset(self, entries: List[Dict], current: Optional[str] = None) -> None:
        """Rebuild the strip from scratch.

        Each entry is a dict of ``key``, ``title``, ``type``, ``modified``
        and optionally ``tooltip`` and ``stale``.
        """
        self._updating = True
        while self.count():
            self.removeTab(0)
        self._keys = []

        self.addTab(icons.icon("iso", 14), "Home")
        self.setTabToolTip(0, "The start page")
        self._keys.append(HOME)
        # Home is never closed, so it gets no close button
        for side in (QtWidgets.QTabBar.RightSide, QtWidgets.QTabBar.LeftSide):
            self.setTabButton(0, side, None)

        for entry in entries:
            label = entry["title"] + (" *" if entry.get("modified") else "")
            index = self.addTab(
                icons.icon(TYPE_ICONS.get(entry.get("type"), "box"), 14),
                label)
            self._keys.append(entry["key"])
            self.setTabToolTip(index, entry.get("tooltip") or entry["title"])
            # the platform close button is a bare square under this palette,
            # so each tab gets one drawn from the same icon set as everything
            # else in the window
            self.setTabButton(index, QtWidgets.QTabBar.LeftSide, None)
            self.setTabButton(index, QtWidgets.QTabBar.RightSide,
                              self._close_button(entry["key"]))
            if entry.get("stale"):
                self.setTabTextColor(index, QtGui.QColor(C.warn))
                self.setTabToolTip(
                    index, "%s\n\nOut of date - press Local Update"
                           % (entry.get("tooltip") or entry["title"]))

        self.select(current or HOME)
        self._updating = False

    def _close_button(self, key: str) -> QtWidgets.QToolButton:
        button = QtWidgets.QToolButton(self)
        button.setIcon(icons.icon("cancel", 12))
        button.setIconSize(QtCore.QSize(9, 9))
        button.setFixedSize(15, 15)
        button.setAutoRaise(True)
        button.setCursor(QtCore.Qt.ArrowCursor)
        button.setToolTip("Close")
        button.clicked.connect(
            lambda _=False, k=key: self.close_requested.emit(k))
        return button

    def select(self, key: str) -> None:
        if key in self._keys:
            self._updating_before = self._updating
            self._updating = True
            self.setCurrentIndex(self._keys.index(key))
            self._updating = self._updating_before

    def key_at(self, index: int) -> Optional[str]:
        if 0 <= index < len(self._keys):
            return self._keys[index]
        return None

    @property
    def current_key(self) -> Optional[str]:
        return self.key_at(self.currentIndex())

    @property
    def keys(self) -> List[str]:
        return [k for k in self._keys if k != HOME]

    # ------------------------------------------------------------ behaviour

    def _changed(self, index: int) -> None:
        if self._updating:
            return
        key = self.key_at(index)
        if key is not None:
            self.document_selected.emit(key)

    def _close_clicked(self, index: int) -> None:
        key = self.key_at(index)
        if key and key != HOME:
            self.close_requested.emit(key)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MiddleButton:
            index = self.tabAt(event.position().toPoint())
            key = self.key_at(index)
            if key and key != HOME:
                self.close_requested.emit(key)
                return
        super().mouseReleaseEvent(event)

    def _menu(self, pos: QtCore.QPoint) -> None:
        index = self.tabAt(pos)
        key = self.key_at(index)
        if key is None or key == HOME:
            return
        menu = QtWidgets.QMenu(self)
        save = menu.addAction(icons.icon("save", 16), "Save")
        save.triggered.connect(lambda: self.save_requested.emit(key))
        menu.addSeparator()
        close = menu.addAction("Close")
        close.triggered.connect(lambda: self.close_requested.emit(key))
        others = menu.addAction("Close All Others")
        others.setEnabled(len(self.keys) > 1)
        others.triggered.connect(lambda: self.close_others_requested.emit(key))
        menu.exec(self.mapToGlobal(pos))
