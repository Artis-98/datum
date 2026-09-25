"""Inventor-style ribbon: a tab strip over panels of command buttons."""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from . import icons
from .theme import C


class RibbonButton(QtWidgets.QToolButton):
    """Large button: icon on top, caption underneath."""

    def __init__(self, name: str, text: str, tip: str = "",
                 checkable: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("RibbonButton")
        self.setIcon(icons.icon(name, 26))
        self.setIconSize(QtCore.QSize(26, 26))
        self.setText(text)
        self.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
        self.setToolTip(tip or text)
        self.setCheckable(checkable)
        self.setAutoRaise(False)
        self.setMinimumWidth(54)
        self.setMaximumWidth(88)
        self.setFixedHeight(68)
        self.setStyleSheet("font-size: 11px;")
        self.setCursor(QtCore.Qt.PointingHandCursor)


class RibbonSmallButton(QtWidgets.QToolButton):
    """Compact button: icon beside caption, stacked three per column."""

    def __init__(self, name: str, text: str, tip: str = "",
                 checkable: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("RibbonSmallButton")
        self.setIcon(icons.icon(name, 17))
        self.setIconSize(QtCore.QSize(17, 17))
        self.setText(" " + text)
        self.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.setToolTip(tip or text)
        self.setCheckable(checkable)
        self.setFixedHeight(20)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setSizePolicy(QtWidgets.QSizePolicy.Minimum,
                           QtWidgets.QSizePolicy.Fixed)


class SplitMenuEntry(QtWidgets.QWidget):
    """One row of a split button's menu: the name over what it does.

    A plain menu action is a single line, and these variants only make sense
    as a pair - "Rectangle" tells you nothing without "Three Point Center"
    underneath it.
    """

    def __init__(self, icon_name: str, title: str, subtitle: str,
                 parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SplitEntry")
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 14, 4)
        layout.setSpacing(9)

        badge = QtWidgets.QLabel()
        badge.setPixmap(icons.pixmap(icon_name, 20, self.devicePixelRatioF()))
        badge.setFixedWidth(22)
        layout.addWidget(badge)

        text = QtWidgets.QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(0)
        head = QtWidgets.QLabel(title)
        head.setStyleSheet("font-weight: 600; color: %s;" % C.text)
        sub = QtWidgets.QLabel(subtitle)
        sub.setStyleSheet("color: %s; font-size: 11px;" % C.text_dim)
        text.addWidget(head)
        text.addWidget(sub)
        layout.addLayout(text, 1)


class RibbonSplitButton(RibbonButton):
    """A big button with a list of variants under an arrow.

    Inventor puts the rectangle, slot and polygon tools behind one button
    like this.  The button keeps showing whichever variant you used last, so
    the common case stays one click and the rest are one click plus a pick.
    """

    chosen = QtCore.Signal(str)

    def __init__(self, options, tip: str = "", parent=None) -> None:
        """``options`` is a list of (key, icon name, title, subtitle)."""
        first = options[0]
        super().__init__(first[1], first[2], tip, True, parent)
        self.options = list(options)
        self.current = first[0]

        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(
            "QMenu { padding: 4px; }"
            " #SplitEntry { background: transparent; }")
        for key, icon_name, title, subtitle in options:
            action = QtWidgets.QWidgetAction(menu)
            entry = SplitMenuEntry(icon_name, title, subtitle, menu)
            action.setDefaultWidget(entry)
            action.triggered.connect(lambda _=False, k=key: self._pick(k))
            menu.addAction(action)
        self.setMenu(menu)
        self.setPopupMode(QtWidgets.QToolButton.MenuButtonPopup)
        # room for the arrow without squeezing the caption
        self.setMaximumWidth(100)
        self.set_current(first[0])
        self.clicked.connect(lambda _=False: self.chosen.emit(self.current))

    @property
    def keys(self):
        return [option[0] for option in self.options]

    def _pick(self, key: str) -> None:
        self.set_current(key)
        self.chosen.emit(key)

    def set_current(self, key: str) -> None:
        for option_key, icon_name, title, subtitle in self.options:
            if option_key != key:
                continue
            self.current = key
            self.setIcon(icons.icon(icon_name, 26))
            # the caption is the variant, because "Rectangle" alone does not
            # say which of the four is armed
            self.setText(title + chr(10) + subtitle)
            self.setToolTip("%s - %s" % (title, subtitle))
            return


class RibbonPanel(QtWidgets.QWidget):
    """A titled group of commands inside a ribbon tab."""

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("RibbonPanel")
        self.title = title

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 1)
        outer.setSpacing(1)

        self.body = QtWidgets.QHBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(2)
        outer.addLayout(self.body, 1)

        label = QtWidgets.QLabel(title)
        label.setObjectName("RibbonPanelTitle")
        label.setAlignment(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignBottom)
        outer.addWidget(label)

        self._column: Optional[QtWidgets.QVBoxLayout] = None
        self._column_count = 0

    def add_big(self, name: str, text: str, tip: str = "",
                checkable: bool = False) -> RibbonButton:
        self._column = None
        btn = RibbonButton(name, text, tip, checkable, self)
        self.body.addWidget(btn, 0, QtCore.Qt.AlignTop)
        return btn

    def add_split(self, options, tip: str = "") -> "RibbonSplitButton":
        """A big button whose arrow opens a list of variants."""
        self._column = None
        btn = RibbonSplitButton(options, tip, self)
        self.body.addWidget(btn, 0, QtCore.Qt.AlignTop)
        return btn

    def add_small(self, name: str, text: str, tip: str = "",
                  checkable: bool = False) -> RibbonSmallButton:
        if self._column is None or self._column_count >= 3:
            self._column = QtWidgets.QVBoxLayout()
            self._column.setContentsMargins(0, 0, 0, 0)
            self._column.setSpacing(1)
            self._column.setAlignment(QtCore.Qt.AlignTop)
            holder = QtWidgets.QWidget(self)
            holder.setLayout(self._column)
            holder.setFixedHeight(68)
            self.body.addWidget(holder, 0, QtCore.Qt.AlignTop)
            self._column_count = 0
        btn = RibbonSmallButton(name, text, tip, checkable, self)
        self._column.addWidget(btn)
        self._column_count += 1
        return btn

    def add_widget(self, widget: QtWidgets.QWidget) -> QtWidgets.QWidget:
        self._column = None
        self.body.addWidget(widget, 0, QtCore.Qt.AlignVCenter)
        return widget

    def add_separator(self) -> None:
        self._column = None
        line = QtWidgets.QFrame(self)
        line.setFrameShape(QtWidgets.QFrame.VLine)
        line.setStyleSheet("color: %s;" % C.border)
        line.setFixedWidth(1)
        self.body.addWidget(line)


class RibbonTab(QtWidgets.QWidget):
    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.title = title
        self._layout = QtWidgets.QHBoxLayout(self)
        self._layout.setContentsMargins(2, 0, 2, 0)
        self._layout.setSpacing(0)
        self._layout.addStretch(1)
        self.panels: List[RibbonPanel] = []

    def add_panel(self, title: str) -> RibbonPanel:
        panel = RibbonPanel(title, self)
        self._layout.insertWidget(self._layout.count() - 1, panel)
        self.panels.append(panel)
        return panel


class Ribbon(QtWidgets.QWidget):
    """The whole ribbon: tab strip plus the stacked tab pages."""

    tab_changed = QtCore.Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("RibbonBar")

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Row 1: the quick access toolbar, on its own line above the tabs,
        # which is where Inventor puts it.
        self.qat_bar = QtWidgets.QWidget(self)
        self.qat_bar.setObjectName("RibbonQat")
        self.qat_bar.setFixedHeight(26)
        qat_layout = QtWidgets.QHBoxLayout(self.qat_bar)
        qat_layout.setContentsMargins(7, 2, 8, 2)
        qat_layout.setSpacing(2)

        self.quick_access = QtWidgets.QHBoxLayout()
        self.quick_access.setContentsMargins(0, 0, 0, 0)
        self.quick_access.setSpacing(1)
        qat_layout.addLayout(self.quick_access)
        qat_layout.addStretch(1)

        # Material and appearance sit up here rather than in a panel,
        # because what a part is made of is a property of the part and not
        # a command you run.  Inventor puts them in the same place.
        self.material_slot = QtWidgets.QHBoxLayout()
        self.material_slot.setContentsMargins(0, 0, 0, 0)
        self.material_slot.setSpacing(0)
        qat_layout.addLayout(self.material_slot)
        qat_layout.addSpacing(12)

        self.document_label = QtWidgets.QLabel("")
        self.document_label.setStyleSheet(
            "color: %s; font-size: 11px;" % C.text_dim)
        qat_layout.addWidget(self.document_label)

        root.addWidget(self.qat_bar)

        # Row 2: the File button and the tab strip.
        self.strip = QtWidgets.QWidget(self)
        self.strip.setObjectName("RibbonTabStrip")
        self.strip.setFixedHeight(27)
        strip_layout = QtWidgets.QHBoxLayout(self.strip)
        strip_layout.setContentsMargins(6, 0, 8, 0)
        strip_layout.setSpacing(1)
        self._strip_layout = strip_layout

        # File behaves like Inventor's: a coloured button holding the document
        # commands, rather than a ribbon tab of its own.
        self.file_button = QtWidgets.QToolButton(self.strip)
        self.file_button.setObjectName("FileButton")
        self.file_button.setText("File")
        self.file_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.file_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.file_button.setStyleSheet(
            "#FileButton { background: %s; color: %s; font-weight: 600;"
            " padding: 6px 15px 5px 15px; border: none; }"
            "#FileButton:hover { background: %s; }"
            "#FileButton::menu-indicator { image: none; width: 0; }"
            % (C.accent_dark, C.on_accent, C.accent))
        strip_layout.addWidget(self.file_button)
        strip_layout.addSpacing(4)
        strip_layout.addStretch(1)

        self.status_hint = QtWidgets.QLabel("")
        self.status_hint.setStyleSheet(
            "color: %s; font-size: 11px;" % C.text_dim)
        strip_layout.addWidget(self.status_hint)

        root.addWidget(self.strip)

        self.stack = QtWidgets.QStackedWidget(self)
        self.stack.setFixedHeight(90)
        root.addWidget(self.stack)

        self._tabs: Dict[str, RibbonTab] = {}
        self._buttons: Dict[str, QtWidgets.QToolButton] = {}
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)

    # -- construction -------------------------------------------------------

    def add_tab(self, title: str, contextual: bool = False) -> RibbonTab:
        tab = RibbonTab(title, self)
        self._tabs[title] = tab
        self.stack.addWidget(tab)

        btn = QtWidgets.QToolButton(self.strip)
        btn.setObjectName("RibbonTabButton")
        btn.setText(title)
        btn.setCheckable(True)
        btn.setCursor(QtCore.Qt.PointingHandCursor)
        if contextual:
            btn.setStyleSheet("#RibbonTabButton:checked { border-bottom: 2px "
                              "solid %s; } #RibbonTabButton { color: %s; }"
                              % (C.warn, C.warn))
            btn.setVisible(False)
        btn.clicked.connect(lambda _=False, t=title: self.show_tab(t))
        self._group.addButton(btn)
        # insert before the stretch + hint
        self._strip_layout.insertWidget(self._strip_layout.count() - 2, btn)
        self._buttons[title] = btn

        if len(self._tabs) == 1:
            btn.setChecked(True)
        return tab

    # -- behaviour ----------------------------------------------------------

    def show_tab(self, title: str) -> None:
        tab = self._tabs.get(title)
        if tab is None:
            return
        self._buttons[title].setVisible(True)
        self._buttons[title].setChecked(True)
        self.stack.setCurrentWidget(tab)
        self.tab_changed.emit(title)

    def set_tab_visible(self, title: str, visible: bool) -> None:
        btn = self._buttons.get(title)
        if btn is not None:
            btn.setVisible(visible)

    def current_tab(self) -> str:
        widget = self.stack.currentWidget()
        return getattr(widget, "title", "")

    def set_hint(self, text: str) -> None:
        self.status_hint.setText(text)

    def set_file_menu(self, menu: QtWidgets.QMenu) -> None:
        self.file_button.setMenu(menu)

    def add_quick_action(self, icon_name: str, tip: str) -> QtWidgets.QToolButton:
        """Small always-visible button beside File, like Inventor's QAT."""
        btn = QtWidgets.QToolButton(self.qat_bar)
        btn.setObjectName("QuickButton")
        btn.setIcon(icons.icon(icon_name, 16))
        btn.setIconSize(QtCore.QSize(16, 16))
        btn.setToolTip(tip)
        btn.setAutoRaise(True)
        btn.setFixedSize(24, 22)
        btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.quick_access.addWidget(btn)
        return btn

    def add_quick_separator(self) -> None:
        line = QtWidgets.QFrame(self.qat_bar)
        line.setFrameShape(QtWidgets.QFrame.VLine)
        line.setFixedWidth(1)
        line.setStyleSheet("color: %s; margin: 3px 5px;" % C.border_light)
        self.quick_access.addWidget(line)

    def set_document_label(self, text: str) -> None:
        self.document_label.setText(text)

