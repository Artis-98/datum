"""The assembly browser: occurrences, with their relationships underneath.

Inventor nests each constraint under the components it holds, so a part shows
you what is pinning it down without hunting through a flat list.  Same here:
every occurrence carries its own relationships, and a constraint therefore
appears twice - once under each of the two parts it relates.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ..core.assembly import AssemblyDocument
from . import icons
from .theme import C

ROLE_ID = QtCore.Qt.UserRole + 1
ROLE_KIND = QtCore.Qt.UserRole + 2

ORIGIN_PLANES = (("XY Plane", "XY"), ("XZ Plane", "XZ"), ("YZ Plane", "YZ"))


class AssemblyBrowser(QtWidgets.QTreeWidget):
    """Tree of placed components and the relationships between them."""

    occurrence_selected = QtCore.Signal(int)
    occurrence_activated = QtCore.Signal(int)        # double-click -> open part
    occurrence_delete_requested = QtCore.Signal(int)
    occurrence_rename_requested = QtCore.Signal(int, str)
    ground_toggled = QtCore.Signal(int)
    visibility_toggled = QtCore.Signal(int)
    suppress_toggled = QtCore.Signal(int)
    isolate_requested = QtCore.Signal(int)
    replace_requested = QtCore.Signal(int)

    constraint_selected = QtCore.Signal(int)
    constraint_activated = QtCore.Signal(int)        # double-click -> edit
    constraint_delete_requested = QtCore.Signal(int)
    constraint_suppress_toggled = QtCore.Signal(int)

    plane_visibility_toggled = QtCore.Signal(str)
    place_requested = QtCore.Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setRootIsDecorated(True)
        self.setIndentation(14)
        self.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.setExpandsOnDoubleClick(False)
        self.setIconSize(QtCore.QSize(16, 16))
        self.setUniformRowHeights(True)

        self.customContextMenuRequested.connect(self._menu)
        self.itemDoubleClicked.connect(self._double_clicked)
        self.itemSelectionChanged.connect(self._selection_changed)

        self._doc: Optional[AssemblyDocument] = None
        self._expanded: Dict[str, bool] = {}
        self.hidden_planes: set = {"XY", "XZ", "YZ"}

    # ------------------------------------------------------------- building

    def set_document(self, doc: AssemblyDocument) -> None:
        self._doc = doc
        self.refresh()

    def refresh(self) -> None:
        doc = self._doc
        if doc is None:
            return

        selected = self.selected_occurrence_ids()
        scroll = self.verticalScrollBar().value()
        self._remember_expansion()
        self.blockSignals(True)
        self.clear()

        root = QtWidgets.QTreeWidgetItem(self, [doc.title])
        root.setIcon(0, icons.icon("pattern", 16))
        root.setData(0, ROLE_KIND, "root")
        font = root.font(0)
        font.setBold(True)
        root.setFont(0, font)

        origin = QtWidgets.QTreeWidgetItem(root, ["Origin"])
        origin.setIcon(0, icons.icon("plane", 16))
        origin.setData(0, ROLE_KIND, "origin")
        origin.setForeground(0, QtGui.QBrush(QtGui.QColor(C.text_dim)))
        for label, key in ORIGIN_PLANES:
            item = QtWidgets.QTreeWidgetItem(origin, [label])
            item.setIcon(0, icons.icon("plane", 16))
            item.setData(0, ROLE_KIND, "plane")
            item.setData(0, ROLE_ID, key)
            if key in self.hidden_planes:
                item.setText(0, "%s   (hidden)" % label)
                item.setForeground(0, QtGui.QBrush(QtGui.QColor("#5d656e")))

        if not doc.occurrences:
            empty = QtWidgets.QTreeWidgetItem(
                root, ["No components yet - use Place Component"])
            empty.setData(0, ROLE_KIND, "empty")
            empty.setForeground(0, QtGui.QBrush(QtGui.QColor(C.text_dim)))
            f = empty.font(0)
            f.setItalic(True)
            empty.setFont(0, f)

        for occurrence in doc.occurrences:
            item = QtWidgets.QTreeWidgetItem(root, [occurrence.label])
            item.setIcon(0, icons.icon(occurrence.icon, 16))
            item.setData(0, ROLE_ID, occurrence.id)
            item.setData(0, ROLE_KIND, "occurrence")
            item.setToolTip(0, occurrence.summary())

            tags = []
            if occurrence.grounded:
                tags.append("grounded")
            if occurrence.suppressed:
                tags.append("suppressed")
            if not occurrence.visible:
                tags.append("hidden")
            if tags:
                item.setText(0, "%s   (%s)" % (occurrence.label,
                                               ", ".join(tags)))

            if occurrence.error:
                item.setForeground(0, QtGui.QBrush(QtGui.QColor(C.error)))
                item.setText(0, occurrence.label + "   !")
                item.setToolTip(0, occurrence.error)
            elif occurrence.suppressed:
                f = item.font(0)
                f.setItalic(True)
                item.setFont(0, f)
                item.setForeground(0, QtGui.QBrush(QtGui.QColor("#666e78")))
            elif occurrence.grounded:
                item.setForeground(0, QtGui.QBrush(QtGui.QColor(C.ok)))

            for constraint in doc.constraints_on(occurrence.id):
                child = QtWidgets.QTreeWidgetItem(item, [constraint.name])
                child.setIcon(0, icons.icon(constraint.icon, 16))
                child.setData(0, ROLE_ID, constraint.id)
                child.setData(0, ROLE_KIND, "constraint")
                child.setToolTip(0, "%s\n%s" % (constraint.name,
                                                constraint.summary()))
                if constraint.error:
                    child.setForeground(0, QtGui.QBrush(QtGui.QColor(C.error)))
                    child.setText(0, constraint.name + "   !")
                elif constraint.suppressed:
                    f = child.font(0)
                    f.setItalic(True)
                    child.setFont(0, f)
                    child.setForeground(0,
                                        QtGui.QBrush(QtGui.QColor("#666e78")))
                else:
                    child.setForeground(0,
                                        QtGui.QBrush(QtGui.QColor(C.text_dim)))

        self.expandItem(root)
        self._restore_expansion()
        self.blockSignals(False)

        self.select_occurrences(selected)
        self.verticalScrollBar().setValue(scroll)

    # -- expansion is remembered so a rebuild does not fold the tree up -----

    def _key(self, item: QtWidgets.QTreeWidgetItem) -> str:
        return "%s:%s" % (item.data(0, ROLE_KIND), item.data(0, ROLE_ID))

    def _remember_expansion(self) -> None:
        for item in self._iter_items():
            if item.childCount():
                self._expanded[self._key(item)] = item.isExpanded()

    def _restore_expansion(self) -> None:
        for item in self._iter_items():
            if item.childCount():
                item.setExpanded(self._expanded.get(self._key(item), True))

    def _iter_items(self):
        it = QtWidgets.QTreeWidgetItemIterator(self)
        while it.value():
            yield it.value()
            it += 1

    # ------------------------------------------------------------ selection

    def selected_occurrence_ids(self) -> List[int]:
        return [int(i.data(0, ROLE_ID)) for i in self.selectedItems()
                if i.data(0, ROLE_KIND) == "occurrence"]

    def selected_constraint_ids(self) -> List[int]:
        return [int(i.data(0, ROLE_ID)) for i in self.selectedItems()
                if i.data(0, ROLE_KIND) == "constraint"]

    def select_occurrences(self, ids: List[int]) -> None:
        if not ids:
            return
        self.blockSignals(True)
        self.clearSelection()
        for item in self._iter_items():
            if (item.data(0, ROLE_KIND) == "occurrence"
                    and item.data(0, ROLE_ID) in ids):
                item.setSelected(True)
        self.blockSignals(False)

    def _selection_changed(self) -> None:
        occurrences = self.selected_occurrence_ids()
        if occurrences:
            self.occurrence_selected.emit(occurrences[0])
            return
        constraints = self.selected_constraint_ids()
        if constraints:
            self.constraint_selected.emit(constraints[0])

    def _double_clicked(self, item: QtWidgets.QTreeWidgetItem,
                        _col: int) -> None:
        kind = item.data(0, ROLE_KIND)
        if kind == "occurrence":
            self.occurrence_activated.emit(int(item.data(0, ROLE_ID)))
        elif kind == "constraint":
            self.constraint_activated.emit(int(item.data(0, ROLE_ID)))
        elif kind == "plane":
            self.plane_visibility_toggled.emit(str(item.data(0, ROLE_ID)))
        elif kind == "empty":
            self.place_requested.emit()

    # ------------------------------------------------------------- commands

    def _menu(self, pos: QtCore.QPoint) -> None:
        item = self.itemAt(pos)
        if item is None or self._doc is None:
            return
        kind = item.data(0, ROLE_KIND)
        menu = QtWidgets.QMenu(self)

        if kind == "occurrence":
            oid = int(item.data(0, ROLE_ID))
            occurrence = self._doc.occurrence(oid)
            if occurrence is None:
                return

            open_part = menu.addAction(icons.icon("open", 16), "Open Part")
            open_part.setToolTip("Edit the file this component places")
            open_part.triggered.connect(
                lambda: self.occurrence_activated.emit(oid))

            replace = menu.addAction(icons.icon("import", 16), "Replace...")
            replace.triggered.connect(
                lambda: self.replace_requested.emit(oid))

            menu.addSeparator()
            ground = menu.addAction("Grounded")
            ground.setCheckable(True)
            ground.setChecked(occurrence.grounded)
            ground.setToolTip("A grounded component never moves when the "
                              "constraints are solved")
            ground.triggered.connect(lambda: self.ground_toggled.emit(oid))

            visible = menu.addAction("Visible")
            visible.setCheckable(True)
            visible.setChecked(occurrence.visible)
            visible.triggered.connect(
                lambda: self.visibility_toggled.emit(oid))

            isolate = menu.addAction("Isolate")
            isolate.setToolTip("Hide everything except this component")
            isolate.triggered.connect(lambda: self.isolate_requested.emit(oid))

            suppress = menu.addAction(
                icons.icon("suppress", 16),
                "Unsuppress" if occurrence.suppressed else "Suppress")
            suppress.triggered.connect(lambda: self.suppress_toggled.emit(oid))

            menu.addSeparator()
            rename = menu.addAction("Rename...")
            rename.triggered.connect(
                lambda: self._rename(oid, occurrence.label))
            delete = menu.addAction(icons.icon("delete", 16), "Delete")
            delete.triggered.connect(
                lambda: self.occurrence_delete_requested.emit(oid))

        elif kind == "constraint":
            cid = int(item.data(0, ROLE_ID))
            constraint = self._doc.constraint(cid)
            if constraint is None:
                return
            edit = menu.addAction(icons.icon("edit", 16), "Edit")
            edit.triggered.connect(lambda: self.constraint_activated.emit(cid))
            menu.addSeparator()
            suppress = menu.addAction(
                icons.icon("suppress", 16),
                "Unsuppress" if constraint.suppressed else "Suppress")
            suppress.triggered.connect(
                lambda: self.constraint_suppress_toggled.emit(cid))
            delete = menu.addAction(icons.icon("delete", 16), "Delete")
            delete.triggered.connect(
                lambda: self.constraint_delete_requested.emit(cid))

        elif kind == "plane":
            key = str(item.data(0, ROLE_ID))
            visible = menu.addAction("Visible")
            visible.setCheckable(True)
            visible.setChecked(key not in self.hidden_planes)
            visible.triggered.connect(
                lambda: self.plane_visibility_toggled.emit(key))

        else:
            place = menu.addAction(icons.icon("import", 16),
                                   "Place Component...")
            place.triggered.connect(self.place_requested.emit)

        menu.exec(self.viewport().mapToGlobal(pos))

    def _rename(self, occurrence_id: int, current: str) -> None:
        text, ok = QtWidgets.QInputDialog.getText(
            self, "Rename Component", "Name:", QtWidgets.QLineEdit.Normal,
            current)
        if ok and text.strip():
            self.occurrence_rename_requested.emit(occurrence_id, text.strip())

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key_Delete:
            for cid in self.selected_constraint_ids():
                self.constraint_delete_requested.emit(cid)
                return
            for oid in self.selected_occurrence_ids():
                self.occurrence_delete_requested.emit(oid)
                return
        elif event.key() == QtCore.Qt.Key_F2:
            ids = self.selected_occurrence_ids()
            if ids and self._doc:
                occurrence = self._doc.occurrence(ids[0])
                if occurrence:
                    self._rename(ids[0], occurrence.label)
            return
        super().keyPressEvent(event)
