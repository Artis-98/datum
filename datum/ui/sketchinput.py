"""On-canvas numeric input for the sketcher.

The viewport is a native OpenCASCADE window, so nothing Qt paints inside it
would be visible.  These are frameless top-level popups positioned at the
cursor instead, which composite over the 3D view on every platform.

Keyboard handling is deliberately *not* done by the widgets: focus stays on
the viewport so the drawing tool keeps receiving mouse movement, and the
sketch editor feeds keystrokes in through :meth:`LiveDimensionBar.type_key`.
That is how CAD packages make heads-up input work while you are still
dragging the geometry out.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Sequence

from PySide6 import QtCore, QtGui, QtWidgets

from .theme import MONO_STACK, C

FIELD_STYLE = """
QLineEdit {
    background: %s;
    border: 1px solid %s;
    border-radius: 2px;
    padding: 2px 6px;
    color: %s;
    font-family: %s;
    font-size: 12px;
    selection-background-color: %s;
    /* a light silver highlight wants dark lettering on it */
    selection-color: #171a20;
}
QLineEdit[active="true"] {
    border: 1px solid %s;
    background: %s;
    color: white;
}
QLineEdit[locked="true"] {
    border: 1px solid %s;
    color: %s;
}
"""


def _styled_field(width: int = 78) -> QtWidgets.QLineEdit:
    field = QtWidgets.QLineEdit()
    field.setFixedWidth(width)
    field.setAlignment(QtCore.Qt.AlignRight)
    field.setFocusPolicy(QtCore.Qt.NoFocus)
    field.setStyleSheet(FIELD_STYLE % (
        C.panel_alt, C.border_light, C.text, MONO_STACK, C.accent,
        C.accent, C.accent_soft, C.ok, C.ok,
    ))
    return field


class ValuePopup(QtWidgets.QWidget):
    """A single field that appears at the cursor to take a dimension value."""

    accepted = QtCore.Signal(str)
    cancelled = QtCore.Signal()

    def __init__(self, parent=None) -> None:
        # A Tool window, not a ToolTip: tooltip windows never take keyboard
        # focus on Windows, so Return would never reach the field.
        super().__init__(parent, QtCore.Qt.Tool
                         | QtCore.Qt.FramelessWindowHint
                         | QtCore.Qt.WindowStaysOnTopHint)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, False)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(5, 4, 5, 4)
        layout.setSpacing(5)

        self.caption = QtWidgets.QLabel("")
        self.caption.setStyleSheet("color: %s; font-size: 11px;" % C.text_dim)
        layout.addWidget(self.caption)

        self.field = QtWidgets.QLineEdit()
        self.field.setFixedWidth(104)
        self.field.setStyleSheet(FIELD_STYLE % (
            C.panel_alt, C.accent, C.text_bright, MONO_STACK, C.accent,
            C.accent, C.accent_soft, C.ok, C.ok,
        ))
        layout.addWidget(self.field)

        self.accept_button = QtWidgets.QToolButton()
        self.accept_button.setText("✓")
        self.accept_button.setToolTip("Accept this dimension (Enter)")
        self.accept_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.accept_button.setFixedSize(24, 24)
        self.accept_button.setStyleSheet(
            "QToolButton { background: %s; color: %s; border: none;"
            " border-radius: 3px; font-size: 15px; font-weight: bold; }"
            "QToolButton:hover { background: %s; }"
            % (C.accent_dark, C.on_accent, C.ok))
        layout.addWidget(self.accept_button)

        self.cancel_button = QtWidgets.QToolButton()
        self.cancel_button.setText("✕")
        self.cancel_button.setToolTip("Cancel (Esc)")
        self.cancel_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.cancel_button.setFixedSize(24, 24)
        self.cancel_button.setStyleSheet(
            "QToolButton { background: transparent; color: %s; border: none;"
            " font-size: 13px; }"
            "QToolButton:hover { color: %s; }" % (C.text_dim, C.error))
        layout.addWidget(self.cancel_button)

        self.setStyleSheet("background: %s; border: 1px solid %s;"
                           % (C.panel, C.border_light))

        self.field.returnPressed.connect(self._accept)
        self.accept_button.clicked.connect(self._accept)
        self.cancel_button.clicked.connect(self._cancel)

    def ask(self, value: str, at: QtCore.QPoint, caption: str = "") -> None:
        self.caption.setText(caption)
        self.caption.setVisible(bool(caption))
        self.field.setText(value)
        self.adjustSize()
        self.move(at + QtCore.QPoint(16, 14))
        self.show()
        self.raise_()
        self.activateWindow()
        self.field.setFocus()
        self.field.selectAll()

    def insert(self, text: str) -> None:
        """Type ``text`` into the field at the caret, and keep the focus.

        Used when a dimension is clicked in the viewport while this box is
        open: the click goes to the viewport, so the field has to be given
        the keyboard back or the next keystroke lands nowhere.
        """
        self.field.insert(text)
        self.raise_()
        self.activateWindow()
        self.field.setFocus()

    def _accept(self) -> None:
        text = self.field.text().strip()
        self.hide()
        if text:
            self.accepted.emit(text)
        else:
            self.cancelled.emit()

    def _cancel(self) -> None:
        self.hide()
        self.cancelled.emit()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key_Escape:
            self._cancel()
            return
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self._accept()
            return
        super().keyPressEvent(event)


class LiveDimensionBar(QtWidgets.QWidget):
    """Heads-up dimension fields shown while geometry is being dragged out.

    Values track the cursor until the user types into a field; typing locks
    that value so the geometry follows the number instead.  Tab moves on,
    Enter commits.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent, QtCore.Qt.ToolTip)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(QtCore.Qt.NoFocus)

        self._layout = QtWidgets.QHBoxLayout(self)
        self._layout.setContentsMargins(5, 4, 5, 4)
        self._layout.setSpacing(4)
        self.setStyleSheet("background: %s; border: 1px solid %s;"
                           % (C.panel, C.border_light))

        self.fields: List[QtWidgets.QLineEdit] = []
        self.labels: List[QtWidgets.QLabel] = []
        self.locked: List[bool] = []
        self.active = 0

    # ------------------------------------------------------------- set-up

    def configure(self, captions: Sequence[str]) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.fields = []
        self.labels = []
        self.locked = [False] * len(captions)
        self.active = 0

        for caption in captions:
            label = QtWidgets.QLabel(caption)
            label.setStyleSheet("color: %s; font-size: 11px;" % C.text_dim)
            self._layout.addWidget(label)
            self.labels.append(label)

            field = _styled_field()
            self._layout.addWidget(field)
            self.fields.append(field)

        self._refresh_styles()

    def _refresh_styles(self) -> None:
        for i, field in enumerate(self.fields):
            field.setProperty("active", i == self.active and not self.locked[i])
            field.setProperty("locked", self.locked[i])
            field.style().unpolish(field)
            field.style().polish(field)

    # ---------------------------------------------------------- behaviour

    def show_at(self, at: QtCore.QPoint) -> None:
        self.adjustSize()
        self.move(at + QtCore.QPoint(18, 18))
        if not self.isVisible():
            self.show()
        self.raise_()

    def track(self, values: Sequence[float], at: QtCore.QPoint) -> None:
        """Update the fields the user has not taken over, and follow the cursor."""
        for i, value in enumerate(values):
            if i < len(self.fields) and not self.locked[i]:
                self.fields[i].setText("%.3f" % value)
        self.show_at(at)

    def type_key(self, key: int, text: str) -> bool:
        """Feed one keystroke in. Returns True when the bar consumed it."""
        if not self.fields:
            return False
        field = self.fields[self.active]

        if key in (QtCore.Qt.Key_Backspace,):
            if not self.locked[self.active]:
                self.locked[self.active] = True
                field.setText("")
            else:
                field.setText(field.text()[:-1])
            self._refresh_styles()
            return True

        if key == QtCore.Qt.Key_Tab:
            if len(self.fields) > 1:
                self.locked[self.active] = True
                self.active = (self.active + 1) % len(self.fields)
                self._refresh_styles()
                return True
            return False

        if text and (text.isdigit() or text in ".,-+*/() " or text.isalpha()):
            if not self.locked[self.active]:
                self.locked[self.active] = True
                field.setText("")
            field.setText(field.text() + ("." if text == "," else text))
            self._refresh_styles()
            return True

        return False

    def values(self) -> List[str]:
        return [f.text().strip() for f in self.fields]

    def any_locked(self) -> bool:
        return any(self.locked)

    def locked_values(self) -> List[Optional[str]]:
        return [f.text().strip() if lock else None
                for f, lock in zip(self.fields, self.locked)]

    def reset(self) -> None:
        self.locked = [False] * len(self.fields)
        self.active = 0
        self._refresh_styles()

    def dismiss(self) -> None:
        self.hide()
        self.reset()
