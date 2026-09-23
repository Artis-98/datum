"""The CAM browser: stock, tool, and the parts laid on the sheet.

Each part carries its cut face and every profile it will cut, so the two
decisions that actually matter - which face is up, and which side of each
line the cutter runs - are one click away rather than buried in a dialog.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ..core.cam import CamDocument
from ..core.toolpath import INSIDE, OUTSIDE, SIDE_LABELS
from . import icons
from .theme import C

ROLE_ID = QtCore.Qt.UserRole + 1
ROLE_KIND = QtCore.Qt.UserRole + 2
ROLE_KEY = QtCore.Qt.UserRole + 3


class CamBrowser(QtWidgets.QTreeWidget):
    """Tree of the sheet, the tool and every part on it."""

    part_selected = QtCore.Signal(int)
    part_activated = QtCore.Signal(int)          # double-click -> open part
    part_delete_requested = QtCore.Signal(int)
    part_rename_requested = QtCore.Signal(int, str)
    mirror_toggled = QtCore.Signal(int)
    suppress_toggled = QtCore.Signal(int)
    rotate_requested = QtCore.Signal(int, float)
    move_requested = QtCore.Signal(int)

    cut_face_requested = QtCore.Signal(int)      # open the cut face property
    flip_requested = QtCore.Signal(int)

    side_changed = QtCore.Signal(int, str, str)  # part, loop key, side

    stock_requested = QtCore.Signal()
    add_requested = QtCore.Signal()

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

        self._doc: Optional[CamDocument] = None
        self._expanded: Dict[str, bool] = {}

    # ------------------------------------------------------------- building

    def set_document(self, doc: CamDocument) -> None:
        self._doc = doc
        self.refresh()

    def refresh(self) -> None:
        doc = self._doc
        if doc is None:
            return

        selected = self.selected_part_ids()
        scroll = self.verticalScrollBar().value()
        self._remember_expansion()
        self.blockSignals(True)
        self.clear()

        stock, tool = doc.stock(), doc.tool()

        root = QtWidgets.QTreeWidgetItem(self, [doc.title])
        root.setIcon(0, icons.icon("export", 16))
        root.setData(0, ROLE_KIND, "root")
        font = root.font(0)
        font.setBold(True)
        root.setFont(0, font)

        sheet_item = QtWidgets.QTreeWidgetItem(
            root, ["Stock   %.0f x %.0f x %.2f mm"
                   % (stock.width, stock.height, stock.thickness)])
        sheet_item.setIcon(0, icons.icon("rect", 16))
        sheet_item.setData(0, ROLE_KIND, "stock")
        sheet_item.setToolTip(0, "Double-click to change the sheet and tool")
        sheet_item.setForeground(0, QtGui.QBrush(QtGui.QColor(C.text_dim)))

        tool_item = QtWidgets.QTreeWidgetItem(
            root, ["Tool   %s" % tool.label()])
        tool_item.setIcon(0, icons.icon("hole", 16))
        tool_item.setData(0, ROLE_KIND, "stock")
        tool_item.setForeground(0, QtGui.QBrush(QtGui.QColor(C.text_dim)))

        if not doc.parts:
            empty = QtWidgets.QTreeWidgetItem(
                root, ["No parts yet - use Add Part"])
            empty.setData(0, ROLE_KIND, "empty")
            empty.setForeground(0, QtGui.QBrush(QtGui.QColor(C.text_dim)))
            f = empty.font(0)
            f.setItalic(True)
            empty.setFont(0, f)

        cuts = {(c.part_id, c.key): c
                for c in doc.last_report.toolpath.passes}

        for placed in doc.parts:
            item = QtWidgets.QTreeWidgetItem(root, [placed.label])
            item.setIcon(0, icons.icon(placed.icon, 16))
            item.setData(0, ROLE_ID, placed.id)
            item.setData(0, ROLE_KIND, "part")
            item.setToolTip(0, placed.summary())

            tags = []
            if placed.mirror:
                tags.append("mirrored")
            if abs(placed.rotation) > 1e-9:
                tags.append("%.4g deg" % placed.rotation)
            if placed.manual:
                tags.append("by hand")
            if placed.suppressed:
                tags.append("suppressed")
            if tags:
                item.setText(0, "%s   (%s)" % (placed.label, ", ".join(tags)))

            if placed.error:
                item.setForeground(0, QtGui.QBrush(QtGui.QColor(C.error)))
                item.setText(0, placed.label + "   !")
                item.setToolTip(0, placed.error)
            elif placed.suppressed:
                f = item.font(0)
                f.setItalic(True)
                item.setFont(0, f)
                item.setForeground(0, QtGui.QBrush(QtGui.QColor("#666e78")))

            face = QtWidgets.QTreeWidgetItem(
                item, ["Cut face   %s" % placed.cut_face.describe()])
            face.setIcon(0, icons.icon("shell", 16))
            face.setData(0, ROLE_ID, placed.id)
            face.setData(0, ROLE_KIND, "cutface")
            face.setToolTip(0, "Double-click to pick a different face, or to "
                               "flip the part over")
            if placed.cut_face.error:
                face.setForeground(0, QtGui.QBrush(QtGui.QColor(C.error)))
            elif placed.cut_face.warning:
                face.setForeground(0, QtGui.QBrush(QtGui.QColor(C.warn)))
                face.setToolTip(0, placed.cut_face.warning)
                face.setText(0, face.text(0) + "   !")
            else:
                face.setForeground(0, QtGui.QBrush(QtGui.QColor(C.text_dim)))

            if placed.profile is None:
                continue
            for loop in placed.profile.loops:
                side = placed.side_for(loop.key, loop.default_side)
                child = QtWidgets.QTreeWidgetItem(
                    item, ["%s   -   %s" % (loop.label,
                                            SIDE_LABELS.get(side, side))])
                child.setIcon(0, icons.icon(
                    "circle" if not loop.outer else "rect", 16))
                child.setData(0, ROLE_ID, placed.id)
                child.setData(0, ROLE_KIND, "loop")
                child.setData(0, ROLE_KEY, loop.key)
                cut = cuts.get((placed.id, loop.key))
                if cut is not None and cut.error:
                    child.setForeground(0, QtGui.QBrush(QtGui.QColor(C.error)))
                    child.setText(0, child.text(0) + "   !")
                    child.setToolTip(0, cut.error)
                else:
                    child.setToolTip(
                        0, "Double-click to cut the other side of this line")

        self.expandItem(root)
        self._restore_expansion()
        self.blockSignals(False)

        self.select_parts(selected)
        self.verticalScrollBar().setValue(scroll)

    # -- expansion survives a rebuild --------------------------------------

    def _key(self, item: QtWidgets.QTreeWidgetItem) -> str:
        return "%s:%s:%s" % (item.data(0, ROLE_KIND), item.data(0, ROLE_ID),
                             item.data(0, ROLE_KEY))

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

    def selected_part_ids(self) -> List[int]:
        out: List[int] = []
        for item in self.selectedItems():
            if item.data(0, ROLE_KIND) in ("part", "cutface", "loop"):
                pid = int(item.data(0, ROLE_ID))
                if pid not in out:
                    out.append(pid)
        return out

    def select_parts(self, ids: List[int]) -> None:
        if not ids:
            return
        self.blockSignals(True)
        self.clearSelection()
        for item in self._iter_items():
            if (item.data(0, ROLE_KIND) == "part"
                    and item.data(0, ROLE_ID) in ids):
                item.setSelected(True)
        self.blockSignals(False)

    def _selection_changed(self) -> None:
        ids = self.selected_part_ids()
        if ids:
            self.part_selected.emit(ids[0])

    def _double_clicked(self, item: QtWidgets.QTreeWidgetItem,
                        _col: int) -> None:
        kind = item.data(0, ROLE_KIND)
        if kind == "part":
            self.part_activated.emit(int(item.data(0, ROLE_ID)))
        elif kind == "cutface":
            self.cut_face_requested.emit(int(item.data(0, ROLE_ID)))
        elif kind == "loop":
            self._toggle_side(item)
        elif kind == "stock":
            self.stock_requested.emit()
        elif kind == "empty":
            self.add_requested.emit()

    def _toggle_side(self, item: QtWidgets.QTreeWidgetItem) -> None:
        doc = self._doc
        if doc is None:
            return
        pid = int(item.data(0, ROLE_ID))
        key = str(item.data(0, ROLE_KEY))
        placed = doc.part(pid)
        if placed is None or placed.profile is None:
            return
        loop = placed.profile.loop(key)
        if loop is None:
            return
        current = placed.side_for(key, loop.default_side)
        self.side_changed.emit(pid, key,
                               INSIDE if current == OUTSIDE else OUTSIDE)

    # ------------------------------------------------------------- commands

    def _menu(self, pos: QtCore.QPoint) -> None:
        item = self.itemAt(pos)
        if item is None or self._doc is None:
            return
        kind = item.data(0, ROLE_KIND)
        menu = QtWidgets.QMenu(self)

        if kind == "part":
            pid = int(item.data(0, ROLE_ID))
            placed = self._doc.part(pid)
            if placed is None:
                return

            open_part = menu.addAction(icons.icon("open", 16), "Open Part")
            open_part.triggered.connect(
                lambda: self.part_activated.emit(pid))
            face = menu.addAction(icons.icon("shell", 16), "Cut Face...")
            face.triggered.connect(lambda: self.cut_face_requested.emit(pid))

            menu.addSeparator()
            move = menu.addAction(icons.icon("move", 16), "Move To...")
            move.triggered.connect(lambda: self.move_requested.emit(pid))
            for label, delta in (("Rotate 90", 90.0), ("Rotate -90", -90.0),
                                 ("Rotate 180", 180.0)):
                action = menu.addAction(icons.icon("revolve", 16), label)
                action.triggered.connect(
                    lambda _=False, d=delta: self.rotate_requested.emit(pid, d))
            mirror = menu.addAction(icons.icon("mirror", 16), "Mirror")
            mirror.setCheckable(True)
            mirror.setChecked(placed.mirror)
            mirror.setToolTip("Turn this copy over on the sheet")
            mirror.triggered.connect(lambda: self.mirror_toggled.emit(pid))

            menu.addSeparator()
            suppress = menu.addAction(
                icons.icon("suppress", 16),
                "Unsuppress" if placed.suppressed else "Suppress")
            suppress.triggered.connect(lambda: self.suppress_toggled.emit(pid))
            rename = menu.addAction("Rename...")
            rename.triggered.connect(lambda: self._rename(pid, placed.label))
            delete = menu.addAction(icons.icon("delete", 16), "Delete")
            delete.triggered.connect(
                lambda: self.part_delete_requested.emit(pid))

        elif kind == "cutface":
            pid = int(item.data(0, ROLE_ID))
            placed = self._doc.part(pid)
            if placed is None:
                return
            pick = menu.addAction(icons.icon("select", 16), "Pick Face...")
            pick.triggered.connect(lambda: self.cut_face_requested.emit(pid))
            flip = menu.addAction("Flip")
            flip.setCheckable(True)
            flip.setChecked(placed.cut_face.flip)
            flip.setToolTip("Read the part off the other side of the sheet, "
                            "which mirrors it")
            flip.triggered.connect(lambda: self.flip_requested.emit(pid))

        elif kind == "loop":
            pid = int(item.data(0, ROLE_ID))
            key = str(item.data(0, ROLE_KEY))
            placed = self._doc.part(pid)
            loop = placed.profile.loop(key) if placed and placed.profile else None
            if loop is None:
                return
            current = placed.side_for(key, loop.default_side)
            for side in (OUTSIDE, INSIDE):
                action = menu.addAction("Cut %s" % SIDE_LABELS[side])
                action.setCheckable(True)
                action.setChecked(current == side)
                action.triggered.connect(
                    lambda _=False, s=side: self.side_changed.emit(pid, key, s))

        elif kind == "stock":
            action = menu.addAction(icons.icon("params", 16),
                                    "Sheet and Tool...")
            action.triggered.connect(self.stock_requested.emit)

        else:
            action = menu.addAction(icons.icon("import", 16), "Add Part...")
            action.triggered.connect(self.add_requested.emit)

        menu.exec(self.viewport().mapToGlobal(pos))

    def _rename(self, part_id: int, current: str) -> None:
        text, ok = QtWidgets.QInputDialog.getText(
            self, "Rename Part", "Name:", QtWidgets.QLineEdit.Normal, current)
        if ok and text.strip():
            self.part_rename_requested.emit(part_id, text.strip())

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key_Delete:
            for pid in self.selected_part_ids():
                self.part_delete_requested.emit(pid)
                return
        elif event.key() == QtCore.Qt.Key_F2:
            ids = self.selected_part_ids()
            if ids and self._doc:
                placed = self._doc.part(ids[0])
                if placed:
                    self._rename(ids[0], placed.label)
            return
        super().keyPressEvent(event)
