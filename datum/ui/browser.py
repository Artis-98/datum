"""The model browser: the feature tree down the left-hand side."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ..core import kernel
from ..core.document import Document
from ..core.features import Feature, SketchFeature
from . import icons
from .theme import C

ROLE_ID = QtCore.Qt.UserRole + 1
ROLE_KIND = QtCore.Qt.UserRole + 2
ROLE_INDEX = QtCore.Qt.UserRole + 3
ROLE_SHARED = QtCore.Qt.UserRole + 4
ROLE_VISIBLE = QtCore.Qt.UserRole + 5

ORIGIN_PLANES = (("XY Plane", "XY"), ("XZ Plane", "XZ"), ("YZ Plane", "YZ"))


class ModelBrowser(QtWidgets.QTreeWidget):
    """Feature tree with the usual right-click verbs."""

    feature_activated = QtCore.Signal(int)          # double-click -> edit
    feature_selected = QtCore.Signal(int)
    plane_activated = QtCore.Signal(str)            # new sketch on a plane
    delete_requested = QtCore.Signal(int)
    rename_requested = QtCore.Signal(int, str)
    suppress_toggled = QtCore.Signal(int)
    share_toggled = QtCore.Signal(int)
    plane_visibility_toggled = QtCore.Signal(str)
    sketch_on_plane_requested = QtCore.Signal(str)
    move_requested = QtCore.Signal(int, int)
    reorder_requested = QtCore.Signal(int, int)     # feature id, new index
    rollback_requested = QtCore.Signal(object)      # index or None
    delete_below_requested = QtCore.Signal()
    visibility_toggled = QtCore.Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setRootIsDecorated(True)
        self.setIndentation(14)
        self.setAlternatingRowColors(False)
        self.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.setExpandsOnDoubleClick(False)
        self.setIconSize(QtCore.QSize(16, 16))
        self.setUniformRowHeights(True)

        # dragging the End of Part marker rolls the model back; dragging a
        # feature reorders the tree
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)

        self.customContextMenuRequested.connect(self._menu)
        self.itemDoubleClicked.connect(self._double_clicked)
        self.itemSelectionChanged.connect(self._selection_changed)

        self._doc: Optional[Document] = None
        self._hidden_sketches: set = set()
        # consumed sketches start hidden, the way Inventor tucks a sketch
        # away under the feature that used it; these were shown again
        self._shown_sketches: set = set()
        self._expanded_state: Dict[str, bool] = {}

    # ------------------------------------------------------------- building

    def set_document(self, doc: Document) -> None:
        self._doc = doc
        self.refresh()

    def refresh(self) -> None:
        doc = self._doc
        if doc is None:
            return

        selected = self.selected_feature_ids()
        scroll = self.verticalScrollBar().value()
        self.blockSignals(True)
        self.clear()

        root = QtWidgets.QTreeWidgetItem(self, [doc.title])
        root.setIcon(0, icons.icon("box", 16))
        root.setData(0, ROLE_KIND, "root")
        font = root.font(0)
        font.setBold(True)
        root.setFont(0, font)

        # Inventor only shows the Solid Bodies folder once there is more
        # than one, and so does this: a single-body part does not need a
        # folder telling it so.
        bodies = [b for b in getattr(doc, "bodies", []) if b.valid]
        if len(bodies) > 1:
            folder = QtWidgets.QTreeWidgetItem(
                root, ["Solid Bodies(%d)" % len(bodies)])
            folder.setIcon(0, icons.icon("box", 16))
            folder.setData(0, ROLE_KIND, "bodies")
            folder.setForeground(0, QtGui.QBrush(QtGui.QColor(C.text_dim)))
            for body in bodies:
                item = QtWidgets.QTreeWidgetItem(folder, [body.name])
                item.setIcon(0, icons.icon("box", 16))
                item.setData(0, ROLE_KIND, "body")
                item.setData(0, ROLE_ID, body.name)
                try:
                    # measured a piece at a time and remembered, so a
                    # refresh after every rebuild does not integrate every
                    # body again
                    item.setToolTip(0, "%s  -  %.3f cm3" % (
                        body.name,
                        kernel.volume_and_centre(body.shape)[0] / 1000.0))
                except Exception:
                    item.setToolTip(0, body.name)
            folder.setExpanded(True)

        origin = QtWidgets.QTreeWidgetItem(root, ["Origin"])
        origin.setIcon(0, icons.icon("plane", 16))
        origin.setData(0, ROLE_KIND, "origin")
        origin.setForeground(0, QtGui.QBrush(QtGui.QColor(C.text_dim)))
        for label, key in ORIGIN_PLANES:
            item = QtWidgets.QTreeWidgetItem(origin, [label])
            item.setIcon(0, icons.icon("plane", 16))
            item.setData(0, ROLE_KIND, "plane")
            item.setData(0, ROLE_ID, key)
            self._mark_plane_visibility(item, doc.plane_visible(key))
            item.setToolTip(0, "Double-click to start a sketch on %s\n"
                               "Right-click to show or hide it" % label)

        rollback = doc.rollback_index
        end: Optional[QtWidgets.QTreeWidgetItem] = None

        def add_end_marker() -> QtWidgets.QTreeWidgetItem:
            marker = QtWidgets.QTreeWidgetItem(root, ["End of Part"])
            marker.setIcon(0, icons.icon("rollback", 16))
            marker.setData(0, ROLE_KIND, "end")
            marker.setForeground(0, QtGui.QBrush(QtGui.QColor(C.accent)))
            f = marker.font(0)
            f.setBold(True)
            marker.setFont(0, f)
            marker.setToolTip(0, "Drag this marker to roll the model back "
                                 "to any point in the tree")
            return marker

        consumers = self._consumers(doc)

        def add_feature_item(parent, feature, index: int, as_shared: bool):
            item = QtWidgets.QTreeWidgetItem(parent, [feature.name])
            icon = ("sketch_shared"
                    if as_shared and isinstance(feature, SketchFeature)
                    else feature.icon)
            item.setIcon(0, icons.icon(icon, 16))
            item.setData(0, ROLE_ID, feature.id)
            item.setData(0, ROLE_KIND, "feature")
            item.setData(0, ROLE_INDEX, index)
            item.setData(0, ROLE_SHARED, as_shared)
            item.setToolTip(0, feature.summary())

            rolled_back = rollback is not None and index >= rollback
            if feature.error:
                item.setForeground(0, QtGui.QBrush(QtGui.QColor(C.error)))
                item.setToolTip(0, "%s\n\n%s" % (feature.summary(),
                                                 feature.error))
                item.setText(0, feature.name + "   !")
            elif feature.suppressed:
                item.setForeground(0, QtGui.QBrush(QtGui.QColor("#666e78")))
                f = item.font(0)
                f.setItalic(True)
                item.setFont(0, f)
            elif rolled_back:
                item.setForeground(0, QtGui.QBrush(QtGui.QColor("#5d656e")))
            elif as_shared:
                item.setForeground(0, QtGui.QBrush(QtGui.QColor(C.ok)))
                item.setToolTip(0, "%s\n\nShared sketch - available to any "
                                   "feature in the tree." % feature.summary())
            elif isinstance(feature, SketchFeature):
                item.setForeground(0, QtGui.QBrush(QtGui.QColor(C.text_dim)))

            if (isinstance(feature, SketchFeature)
                    and feature.id in self._hidden_sketches
                    and consumers.get(feature.id) is None):
                item.setText(0, item.text(0) + "  (hidden)")
            return item

        for index, feature in enumerate(doc.features):
            consumed_by = consumers.get(feature.id)

            # A consumed sketch lives under the feature that used it. It only
            # keeps a top-level row as well when it has been shared.
            if consumed_by is not None:
                if self.is_shared(feature):
                    if rollback is not None and index == rollback and end is None:
                        end = add_end_marker()
                    add_feature_item(root, feature, index, as_shared=True)
                continue

            if rollback is not None and index == rollback and end is None:
                end = add_end_marker()

            item = add_feature_item(root, feature, index, as_shared=False)

            for child_index, child in self._consumed_by(doc, consumers,
                                                        feature.id):
                add_feature_item(item, child, child_index, as_shared=False)
            if item.childCount():
                item.setExpanded(False)

        if end is None:
            end = add_end_marker()

        self.expandItem(root)
        self.blockSignals(False)

        self.select_features(selected)
        self.verticalScrollBar().setValue(scroll)

    @staticmethod
    def _mark_plane_visibility(item: QtWidgets.QTreeWidgetItem,
                               visible: bool) -> None:
        """Dim a plane row and tag it when it is switched off."""
        item.setData(0, ROLE_VISIBLE, visible)
        if visible:
            item.setForeground(0, QtGui.QBrush(QtGui.QColor(C.text)))
        else:
            item.setForeground(0, QtGui.QBrush(QtGui.QColor("#5d656e")))
            item.setText(0, item.text(0) + "   (hidden)")

    # ------------------------------------------------------- sketch nesting

    @staticmethod
    def _consumers(doc: Document) -> Dict[int, int]:
        """sketch feature id -> id of the first feature that consumes it."""
        sketch_ids = {f.id for f in doc.sketch_features()}
        out: Dict[int, int] = {}
        for feature in doc.features:
            if isinstance(feature, SketchFeature):
                continue
            for dependency in feature.depends_on():
                if dependency in sketch_ids and dependency not in out:
                    out[dependency] = feature.id
        return out

    @staticmethod
    def _consumed_by(doc: Document, consumers: Dict[int, int],
                     feature_id: int) -> List[Tuple[int, Feature]]:
        """(document index, sketch) pairs nested under ``feature_id``."""
        out = []
        for index, feature in enumerate(doc.features):
            if consumers.get(feature.id) == feature_id:
                out.append((index, feature))
        return out

    def is_shared(self, feature: Feature,
                  doc: Optional[Document] = None) -> bool:
        """A sketch is shared when flagged, or when two features want it."""
        doc = doc or self._doc
        if not isinstance(feature, SketchFeature) or doc is None:
            return False
        if feature.shared:
            return True
        users = sum(1 for f in doc.features
                    if not isinstance(f, SketchFeature)
                    and feature.id in f.depends_on())
        return users > 1

    # ------------------------------------------------------------ selection

    def selected_feature_ids(self) -> List[int]:
        out = []
        for item in self.selectedItems():
            if item.data(0, ROLE_KIND) == "feature":
                out.append(int(item.data(0, ROLE_ID)))
        return out

    def select_features(self, ids: List[int]) -> None:
        if not ids:
            return
        self.blockSignals(True)
        self.clearSelection()
        for item in self._iter_items():
            if (item.data(0, ROLE_KIND) == "feature"
                    and item.data(0, ROLE_ID) in ids):
                item.setSelected(True)
        self.blockSignals(False)

    def _iter_items(self):
        it = QtWidgets.QTreeWidgetItemIterator(self)
        while it.value():
            yield it.value()
            it += 1

    def _selection_changed(self) -> None:
        ids = self.selected_feature_ids()
        if ids:
            self.feature_selected.emit(ids[0])

    def is_sketch_hidden(self, feature_id: int,
                         doc: Optional[Document] = None) -> bool:
        """Hidden by hand, or used by a feature and not shown again.

        A shared sketch stays visible: sharing is how you say a sketch is
        still wanted on its own.
        """
        if feature_id in self._hidden_sketches:
            return True
        if feature_id in self._shown_sketches:
            return False
        doc = doc or self._doc
        if doc is None or feature_id not in self._consumers(doc):
            return False
        feature = doc.feature(feature_id)
        return not self.is_shared(feature, doc)

    def toggle_sketch_visibility(self, feature_id: int) -> None:
        if self.is_sketch_hidden(feature_id):
            self._hidden_sketches.discard(feature_id)
            self._shown_sketches.add(feature_id)
        else:
            self._hidden_sketches.add(feature_id)
            self._shown_sketches.discard(feature_id)
        self.refresh()
        self.visibility_toggled.emit(feature_id)

    # ------------------------------------------------------------- commands

    def _double_clicked(self, item: QtWidgets.QTreeWidgetItem, _col: int) -> None:
        kind = item.data(0, ROLE_KIND)
        if kind == "feature":
            from ..core.features import WorkPlaneFeature

            fid = int(item.data(0, ROLE_ID))
            feature = self._doc.feature(fid) if self._doc else None
            # a plane is something you sketch on, so that is what opening it
            # should do; redefining it lives on the right-click menu
            if isinstance(feature, WorkPlaneFeature):
                self.sketch_on_plane_requested.emit(feature.name)
            else:
                self.feature_activated.emit(fid)
        elif kind == "plane":
            self.plane_activated.emit(str(item.data(0, ROLE_ID)))
        elif kind == "end":
            self.rollback_requested.emit(None)

    def _menu(self, pos: QtCore.QPoint) -> None:
        item = self.itemAt(pos)
        if item is None or self._doc is None:
            return
        kind = item.data(0, ROLE_KIND)
        menu = QtWidgets.QMenu(self)

        if kind == "plane":
            key = str(item.data(0, ROLE_ID))
            act = menu.addAction(icons.icon("sketch", 16), "New Sketch")
            act.triggered.connect(lambda: self.plane_activated.emit(key))
            menu.addSeparator()
            self._add_visibility_action(menu, key)

        elif kind == "feature":
            fid = int(item.data(0, ROLE_ID))
            feature = self._doc.feature(fid)
            if feature is None:
                return

            from ..core.features import WorkPlaneFeature

            if isinstance(feature, WorkPlaneFeature):
                sketch_on = menu.addAction(icons.icon("sketch", 16),
                                           "New Sketch")
                sketch_on.triggered.connect(
                    lambda: self.sketch_on_plane_requested.emit(feature.name))
                menu.addSeparator()
                self._add_visibility_action(menu, feature.name)
                menu.addSeparator()
                edit = menu.addAction(icons.icon("edit", 16), "Edit Plane")
                edit.triggered.connect(lambda: self.feature_activated.emit(fid))
            else:
                edit = menu.addAction(icons.icon("edit", 16), "Edit Feature")
                edit.triggered.connect(lambda: self.feature_activated.emit(fid))

            if isinstance(feature, SketchFeature):
                vis = menu.addAction(
                    icons.icon("sketch", 16),
                    "Show Sketch" if self.is_sketch_hidden(fid)
                    else "Hide Sketch")
                vis.triggered.connect(lambda: self.toggle_sketch_visibility(fid))

                consumed = fid in self._consumers(self._doc)
                share = menu.addAction(
                    icons.icon("sketch_shared", 16),
                    "Unshare Sketch" if feature.shared else "Share Sketch")
                share.setEnabled(consumed or feature.shared)
                share.setToolTip(
                    "A shared sketch stays visible at the top of the tree so "
                    "other features can use it too.")
                share.triggered.connect(lambda: self.share_toggled.emit(fid))

            rename = menu.addAction("Rename...")
            rename.triggered.connect(lambda: self._rename(fid, feature.name))

            menu.addSeparator()
            label = "Unsuppress" if feature.suppressed else "Suppress"
            sup = menu.addAction(icons.icon("suppress", 16), label)
            sup.triggered.connect(lambda: self.suppress_toggled.emit(fid))

            index = self._doc.index_of(fid)
            roll = menu.addAction(icons.icon("rollback", 16), "Roll to Here")
            roll.triggered.connect(
                lambda: self.rollback_requested.emit(index))
            if self._doc.rollback_index is not None:
                back = menu.addAction("Roll to End")
                back.triggered.connect(lambda: self.rollback_requested.emit(None))

            menu.addSeparator()
            up = menu.addAction("Move Up")
            up.setEnabled(index > 0)
            up.triggered.connect(lambda: self.move_requested.emit(fid, -1))
            down = menu.addAction("Move Down")
            down.setEnabled(index < len(self._doc.features) - 1)
            down.triggered.connect(lambda: self.move_requested.emit(fid, 1))

            menu.addSeparator()
            dele = menu.addAction(icons.icon("delete", 16), "Delete")
            dele.triggered.connect(lambda: self.delete_requested.emit(fid))

        elif kind == "end":
            act = menu.addAction(icons.icon("rollback", 16), "Roll to End")
            act.triggered.connect(lambda: self.rollback_requested.emit(None))
            rolled = self._doc.rollback_index
            menu.addSeparator()
            drop = menu.addAction(icons.icon("delete", 16),
                                  "Delete All Features Below")
            drop.setEnabled(rolled is not None
                            and rolled < len(self._doc.features))
            drop.triggered.connect(self.delete_below_requested.emit)
        else:
            return

        menu.exec(self.viewport().mapToGlobal(pos))

    def _add_visibility_action(self, menu: QtWidgets.QMenu, name: str) -> None:
        """A checkable Visible entry - the tick shows the current state."""
        if self._doc is None:
            return
        action = menu.addAction("Visible")
        action.setCheckable(True)
        action.setChecked(self._doc.plane_visible(name))
        action.triggered.connect(
            lambda: self.plane_visibility_toggled.emit(name))

    def _rename(self, fid: int, current: str) -> None:
        text, ok = QtWidgets.QInputDialog.getText(
            self, "Rename Feature", "Name:", QtWidgets.QLineEdit.Normal, current)
        if ok and text.strip():
            self.rename_requested.emit(fid, text.strip())

    # ---------------------------------------------------------- drag & drop

    def _feature_row(self, item: Optional[QtWidgets.QTreeWidgetItem]) -> int:
        """The document index an item stands for.

        Rows carry their index explicitly because nested sketches mean the
        tree order no longer matches the feature list one-for-one.
        """
        if self._doc is None or item is None:
            return -1
        kind = item.data(0, ROLE_KIND)
        if kind == "feature":
            index = item.data(0, ROLE_INDEX)
            return -1 if index is None else int(index)
        if kind == "end":
            root = self.topLevelItem(0)
            if root is None:
                return -1
            # the marker's index is that of the top-level row after it
            found = False
            for i in range(root.childCount()):
                child = root.child(i)
                if child is item:
                    found = True
                    continue
                if found and child.data(0, ROLE_KIND) == "feature":
                    return int(child.data(0, ROLE_INDEX))
            return len(self._doc.features)
        return -1

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        item = self.currentItem()
        kind = item.data(0, ROLE_KIND) if item else None
        if kind in ("end", "feature"):
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:
        target = self.itemAt(event.position().toPoint())
        if target is not None and target.data(0, ROLE_KIND) in ("feature", "end"):
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        """Handle the move ourselves - the tree is rebuilt from the document."""
        source = self.currentItem()
        target = self.itemAt(event.position().toPoint())
        event.setDropAction(QtCore.Qt.IgnoreAction)
        event.accept()

        if source is None or target is None or self._doc is None:
            return

        row = self._feature_row(target)
        if row < 0:
            return
        position = self.dropIndicatorPosition()
        below = position in (
            QtWidgets.QAbstractItemView.BelowItem,
            QtWidgets.QAbstractItemView.OnViewport)

        kind = source.data(0, ROLE_KIND)
        if kind == "end":
            # Let go of the marker on a feature and it goes after that
            # feature, so the feature is in: that is what Inventor does,
            # and what anyone dragging it down a row expects.  Only the
            # thin line above a row puts it before.
            after = below or position == QtWidgets.QAbstractItemView.OnItem
            index = row + (1 if after else 0)
            total = len(self._doc.features)
            self.rollback_requested.emit(None if index >= total else index)
        elif kind == "feature":
            fid = int(source.data(0, ROLE_ID))
            if target.data(0, ROLE_KIND) == "end":
                new_index = len(self._doc.features) - 1
            else:
                new_index = row + (1 if below else 0)
                if self._doc.index_of(fid) < new_index:
                    new_index -= 1
            self.reorder_requested.emit(fid, new_index)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key_Delete:
            for fid in self.selected_feature_ids():
                self.delete_requested.emit(fid)
                return
        elif event.key() == QtCore.Qt.Key_F2:
            ids = self.selected_feature_ids()
            if ids and self._doc:
                feature = self._doc.feature(ids[0])
                if feature:
                    self._rename(ids[0], feature.name)
            return
        super().keyPressEvent(event)
