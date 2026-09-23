"""The drawing tree: resources, then every sheet and what is on it."""

from __future__ import annotations

from typing import List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ..core import drawing as dwg
from ..core.drawing import DrawingDocument, Sheet, View
from . import icons
from .theme import C

ROLE_KIND = QtCore.Qt.UserRole + 1      # sheet, view, note, resource, group
ROLE_ID = QtCore.Qt.UserRole + 2

KIND_ICONS = {
    dwg.BASE: "box", dwg.PROJECTED: "pattern", dwg.SECTION: "section",
    dwg.DETAIL: "measure", dwg.AUXILIARY: "plane",
}


class DrawingBrowser(QtWidgets.QTreeWidget):
    """Mirrors the document, and is how sheets are switched."""

    sheet_activated = QtCore.Signal(int)
    view_selected = QtCore.Signal(int)
    view_activated = QtCore.Signal(int)
    annotation_selected = QtCore.Signal(int)
    context_requested = QtCore.Signal(QtCore.QPoint)

    def __init__(self, host) -> None:
        super().__init__(host)
        self.host = host
        self.doc: Optional[DrawingDocument] = None
        self.setHeaderHidden(True)
        self.setIndentation(14)
        self.setExpandsOnDoubleClick(False)
        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.context_requested.emit)
        self.itemSelectionChanged.connect(self._selected)
        self.itemDoubleClicked.connect(self._activated)

    # ------------------------------------------------------------- building

    def set_document(self, doc: Optional[DrawingDocument]) -> None:
        self.doc = doc
        self.refresh()

    def refresh(self) -> None:
        expanded = self._expanded()
        selected = self._selection()
        self.blockSignals(True)
        self.clear()
        doc = self.doc
        if doc is not None:
            self._resources(doc)
            for index, sheet in enumerate(doc.sheets):
                self._sheet(doc, sheet, index)
        self.blockSignals(False)
        self._restore(expanded, selected)

    def _resources(self, doc: DrawingDocument) -> None:
        root = QtWidgets.QTreeWidgetItem(self, ["Drawing Resources"])
        root.setData(0, ROLE_KIND, "group")
        root.setIcon(0, icons.icon("params", 16))
        for title, names in (("Borders", sorted(doc.borders)),
                             ("Title Blocks", sorted(doc.title_blocks))):
            group = QtWidgets.QTreeWidgetItem(root, [title])
            group.setData(0, ROLE_KIND, "group")
            for name in names:
                item = QtWidgets.QTreeWidgetItem(group, [name])
                item.setData(0, ROLE_KIND, "resource")
                item.setIcon(0, icons.icon("open", 16))

    def _sheet(self, doc: DrawingDocument, sheet: Sheet, index: int) -> None:
        active = sheet.id == doc.active_sheet
        label = "%s  (%s)" % (sheet.name, sheet.size)
        item = QtWidgets.QTreeWidgetItem(self, [label])
        item.setData(0, ROLE_KIND, "sheet")
        item.setData(0, ROLE_ID, sheet.id)
        item.setIcon(0, icons.icon("export", 16))
        if active:
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
            item.setForeground(0, QtGui.QBrush(QtGui.QColor(C.ok)))

        if sheet.border:
            child = QtWidgets.QTreeWidgetItem(item, [sheet.border + " border"])
            child.setData(0, ROLE_KIND, "border")
            child.setData(0, ROLE_ID, sheet.id)
        if sheet.title_block:
            child = QtWidgets.QTreeWidgetItem(
                item, [sheet.title_block + " title block"])
            child.setData(0, ROLE_KIND, "title_block")
            child.setData(0, ROLE_ID, sheet.id)

        # views nest under their parents, the way they are derived
        placed = {}
        for view in sheet.views:
            if view.parent and any(v.id == view.parent for v in sheet.views):
                continue
            placed[view.id] = self._view(item, doc, sheet, view)
        remaining = [v for v in sheet.views if v.id not in placed]
        guard = 0
        while remaining and guard < 50:
            guard += 1
            for view in list(remaining):
                parent_item = placed.get(view.parent)
                if parent_item is None:
                    continue
                placed[view.id] = self._view(parent_item, doc, sheet, view)
                remaining.remove(view)
        for view in remaining:          # a broken parent link: show it anyway
            placed[view.id] = self._view(item, doc, sheet, view)

        for table in sheet.parts_lists:
            child = QtWidgets.QTreeWidgetItem(
                item, ["Parts List   %d item(s)" % len(table.rows)])
            child.setData(0, ROLE_KIND, "parts_list")
            child.setData(0, ROLE_ID, table.id)
            child.setIcon(0, icons.icon("material", 16))
            if table.error:
                child.setForeground(0, QtGui.QBrush(QtGui.QColor(C.error)))
                child.setToolTip(0, table.error)

    def _view(self, parent, doc: DrawingDocument, sheet: Sheet,
              view: View) -> QtWidgets.QTreeWidgetItem:
        text = view.label
        if view.scale_visible or view.scale:
            text += "   %s" % dwg.scale_text(doc.view_scale(sheet, view))
        item = QtWidgets.QTreeWidgetItem(parent, [text])
        item.setData(0, ROLE_KIND, "view")
        item.setData(0, ROLE_ID, view.id)
        item.setIcon(0, icons.icon(KIND_ICONS.get(view.kind, "box"), 16))
        if view.error:
            item.setForeground(0, QtGui.QBrush(QtGui.QColor(C.error)))
            item.setToolTip(0, view.error)
        elif view.stale:
            item.setForeground(0, QtGui.QBrush(QtGui.QColor(C.warn)))
            item.setToolTip(0, "The model has changed - press Update")

        for note in sheet.annotations_for(view.id):
            child = QtWidgets.QTreeWidgetItem(
                item, [dwg.ANNOTATION_LABELS.get(note.kind, "Annotation")
                       + "  " + note.caption(doc.view_scale(sheet, view))])
            child.setData(0, ROLE_KIND, "note")
            child.setData(0, ROLE_ID, note.id)
            child.setIcon(0, icons.icon(
                "c_coincident" if note.kind == dwg.BALLOON else "dimension",
                16))
            if note.sick:
                child.setForeground(0, QtGui.QBrush(QtGui.QColor(C.error)))
                child.setToolTip(0, "What this was attached to is gone")
        return item

    # ------------------------------------------------------------- selection

    def _selected(self) -> None:
        item = self.currentItem()
        if item is None:
            return
        kind = item.data(0, ROLE_KIND)
        ident = item.data(0, ROLE_ID)
        if kind == "view" and ident:
            self.view_selected.emit(int(ident))
        elif kind in ("note", "parts_list") and ident:
            self.annotation_selected.emit(int(ident))

    def _activated(self, item: QtWidgets.QTreeWidgetItem, _column: int) -> None:
        kind = item.data(0, ROLE_KIND)
        ident = item.data(0, ROLE_ID)
        if kind == "sheet" and ident:
            self.sheet_activated.emit(int(ident))
        elif kind == "view" and ident:
            self.view_activated.emit(int(ident))
        else:
            item.setExpanded(not item.isExpanded())

    def selected_kind(self):
        item = self.currentItem()
        if item is None:
            return (None, None)
        return (item.data(0, ROLE_KIND), item.data(0, ROLE_ID))

    def select_view(self, view_id: int) -> None:
        for item in self._walk():
            if (item.data(0, ROLE_KIND) == "view"
                    and item.data(0, ROLE_ID) == view_id):
                self.blockSignals(True)
                self.setCurrentItem(item)
                self.blockSignals(False)
                return

    # -------------------------------------------------------------- helpers

    def _walk(self) -> List[QtWidgets.QTreeWidgetItem]:
        out: List[QtWidgets.QTreeWidgetItem] = []
        stack = [self.topLevelItem(i) for i in range(self.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if item is None:
                continue
            out.append(item)
            stack.extend(item.child(i) for i in range(item.childCount()))
        return out

    def _expanded(self):
        return {(i.data(0, ROLE_KIND), i.data(0, ROLE_ID), i.text(0))
                for i in self._walk() if i.isExpanded()}

    def _selection(self):
        item = self.currentItem()
        return ((item.data(0, ROLE_KIND), item.data(0, ROLE_ID))
                if item else None)

    def _restore(self, expanded, selected) -> None:
        for item in self._walk():
            key = (item.data(0, ROLE_KIND), item.data(0, ROLE_ID),
                   item.text(0))
            if key in expanded or item.data(0, ROLE_KIND) in ("sheet",):
                item.setExpanded(True)
            if selected and (item.data(0, ROLE_KIND),
                             item.data(0, ROLE_ID)) == selected:
                self.blockSignals(True)
                self.setCurrentItem(item)
                self.blockSignals(False)
