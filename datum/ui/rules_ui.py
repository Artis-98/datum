"""dLogic: the panel of rules, and the window you write one in.

Laid out like Inventor's iLogic browser because that is what people will
be looking for: the document at the top, its rules under it, double-click
to open one. What is different is the line across the top saying the rules
have not been allowed to run, because a document carrying code is a
document that can do something to your machine, and the file is not the
one who gets to decide that.
"""

from __future__ import annotations

from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ..core import rules as core
from . import icons
from .theme import C

ROLE_ID = QtCore.Qt.UserRole + 1
ROLE_KIND = QtCore.Qt.UserRole + 2

# What a new rule starts as.  It is the documentation most people will
# ever read, so it shows the three things worth knowing in the order they
# are worth knowing them: read a parameter, drive one, and ask with a
# window.  docs/DLOGIC.md has the rest.
STARTER = '''"""What this rule does."""

# Read a parameter, and set one to drive the model:
#     params.width = params.height * 2
#
# Ask, with sliders that move the model as you drag:
#     form("Size",
#          slider("width", 20, 200, 5),
#          slider("height", 10, 120, 5))
#
# Turn a feature off:
#     features["Counterbore"].suppressed = params.thickness < 6

log("parameters:", ", ".join(params.names()) or "none yet")
'''


class LineNumbers(QtWidgets.QWidget):
    """The margin down the left of the editor."""

    def __init__(self, editor: "CodeEditor") -> None:
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        self.editor.paint_gutter(self, event)


class CodeEditor(QtWidgets.QPlainTextEdit):
    """Enough of an editor to write a rule in: numbers, tabs, monospace."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        font = QtGui.QFontDatabase.systemFont(
            QtGui.QFontDatabase.FixedFont)
        font.setPixelSize(12)
        self.setFont(font)
        self.setTabStopDistance(
            4 * QtGui.QFontMetricsF(font).horizontalAdvance(" "))
        self.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.gutter = LineNumbers(self)
        self.blockCountChanged.connect(lambda _: self._resize_gutter())
        self.updateRequest.connect(self._scroll_gutter)
        self._resize_gutter()

    def gutter_width(self) -> int:
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 12 + QtGui.QFontMetricsF(self.font()).horizontalAdvance("9") \
            * digits

    def _resize_gutter(self) -> None:
        self.setViewportMargins(int(self.gutter_width()), 0, 0, 0)

    def _scroll_gutter(self, rect: QtCore.QRect, dy: int) -> None:
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        box = self.contentsRect()
        self.gutter.setGeometry(QtCore.QRect(box.left(), box.top(),
                                             int(self.gutter_width()),
                                             box.height()))

    def paint_gutter(self, widget: QtWidgets.QWidget,
                     event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(widget)
        painter.fillRect(event.rect(), QtGui.QColor(C.panel))
        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = self.blockBoundingGeometry(block).translated(
            self.contentOffset()).top()
        painter.setPen(QtGui.QColor(C.text_dim))
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible():
                painter.drawText(
                    0, int(top), widget.width() - 6,
                    int(self.blockBoundingRect(block).height()),
                    QtCore.Qt.AlignRight, str(number + 1))
            top += self.blockBoundingRect(block).height()
            block = block.next()
            number += 1
        painter.end()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key_Tab:
            self.insertPlainText("    ")
            return
        super().keyPressEvent(event)


class RuleEditor(QtWidgets.QDialog):
    """Write a rule, run it, see what it said."""

    def __init__(self, rule: core.Rule, document, parent=None) -> None:
        super().__init__(parent)
        self.rule = rule
        self.document = document
        self.setWindowTitle("dLogic rule")
        self.setWindowIcon(icons.icon("params", 24))
        self.resize(760, 620)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("Name"))
        self.name_edit = QtWidgets.QLineEdit(rule.name)
        top.addWidget(self.name_edit, 1)
        self.on_rebuild = QtWidgets.QCheckBox("Run after every rebuild")
        self.on_rebuild.setChecked(rule.on_rebuild)
        top.addWidget(self.on_rebuild)
        layout.addLayout(top)

        self.editor = CodeEditor()
        self.editor.setPlainText(rule.source or STARTER)
        layout.addWidget(self.editor, 1)

        self.output = QtWidgets.QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumHeight(140)
        self.output.setPlaceholderText(
            "Run the rule and whatever it logs appears here.")
        layout.addWidget(self.output)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        run = QtWidgets.QPushButton("Run")
        run.setIcon(icons.icon("update", 16))
        run.clicked.connect(self.run_now)
        buttons.addButton(run, QtWidgets.QDialogButtonBox.ActionRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def gather(self) -> core.Rule:
        self.rule.name = self.name_edit.text().strip() or self.rule.name
        self.rule.source = self.editor.toPlainText()
        self.rule.on_rebuild = self.on_rebuild.isChecked()
        return self.rule

    def run_now(self) -> None:
        """Run what is in the box, whether or not it has been saved."""
        rule = core.Rule(name=self.name_edit.text().strip() or "Rule",
                         source=self.editor.toPlainText())
        result = core.run(rule, self.document)
        text = result.output
        if not result.ok:
            where = "line %d: " % result.line if result.line else ""
            text = (text + "\n" if text else "") + where + result.error
        elif result.changed:
            text = (text + "\n" if text else "") + \
                "set " + ", ".join(result.changed)
        self.output.setPlainText(text or "(nothing to say)")
        self.output.setStyleSheet(
            "color: %s;" % (C.error if not result.ok else C.text))


class RulesPanel(QtWidgets.QWidget):
    """The dLogic dock: this document, and the rules it carries."""

    changed = QtCore.Signal()          # a rule ran and moved something

    def __init__(self, host) -> None:
        super().__init__(host)
        self.host = host
        self._document = None

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.banner = QtWidgets.QWidget(self)
        self.banner.setObjectName("BrowserModeBar")
        self.banner.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        row = QtWidgets.QHBoxLayout(self.banner)
        row.setContentsMargins(8, 5, 8, 5)
        row.setSpacing(8)
        self.banner_text = QtWidgets.QLabel("")
        self.banner_text.setWordWrap(True)
        self.banner_text.setStyleSheet("color: %s; font-size: 11px;" % C.warn)
        row.addWidget(self.banner_text, 1)
        self.allow = QtWidgets.QPushButton("Allow")
        self.allow.setFixedHeight(22)
        self.allow.clicked.connect(self.trust)
        row.addWidget(self.allow)
        layout.addWidget(self.banner)

        self.tree = QtWidgets.QTreeWidget(self)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        self.tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        layout.addWidget(self.tree, 1)

        bar = QtWidgets.QHBoxLayout()
        bar.setContentsMargins(6, 4, 6, 6)
        bar.setSpacing(4)
        for text, slot in (("New rule", self.new_rule),
                           ("Run all", self.run_all)):
            button = QtWidgets.QPushButton(text)
            button.setFixedHeight(24)
            button.clicked.connect(slot)
            bar.addWidget(button)
        bar.addStretch(1)
        layout.addLayout(bar)

        self.refresh()

    # -- state ------------------------------------------------------------

    def set_document(self, document) -> None:
        self._document = document if hasattr(document, "rules") else None
        self.refresh()

    @property
    def rules(self) -> Optional[core.RuleSet]:
        return getattr(self._document, "rules", None)

    def refresh(self) -> None:
        self.tree.clear()
        rules = self.rules
        self.setEnabled(self._document is not None)
        if rules is None:
            self.banner.hide()
            return

        title = getattr(self._document, "title", "") or "This document"
        root = QtWidgets.QTreeWidgetItem(self.tree, [title])
        root.setIcon(0, icons.icon("file", 16))
        root.setData(0, ROLE_KIND, "document")
        font = root.font(0)
        font.setBold(True)
        root.setFont(0, font)

        for rule in rules:
            item = QtWidgets.QTreeWidgetItem(root, [rule.name])
            item.setIcon(0, icons.icon("params", 16))
            item.setData(0, ROLE_ID, rule.id)
            item.setData(0, ROLE_KIND, "rule")
            marks = []
            if not rule.enabled:
                marks.append("off")
            if not rule.on_rebuild:
                marks.append("manual")
            if marks:
                item.setText(0, "%s   (%s)" % (rule.name, ", ".join(marks)))
                item.setForeground(0, QtGui.QBrush(QtGui.QColor(C.text_dim)))
        self.tree.expandAll()

        untrusted = len(rules) and not rules.trusted
        self.banner.setVisible(bool(untrusted))
        if untrusted:
            one = len(rules) == 1
            self.banner_text.setText(
                "%d rule%s in this document %s not been allowed to run."
                % (len(rules), "" if one else "s", "has" if one else "have"))

    def trust(self) -> None:
        rules = self.rules
        if rules is None:
            return
        rules.trusted = True
        self.refresh()
        self.host.status_message.setStyleSheet("")
        self.host.status_message.setText(
            "Rules in this document may now run.")

    # -- commands ----------------------------------------------------------

    def selected_rule(self) -> Optional[core.Rule]:
        item = self.tree.currentItem()
        rules = self.rules
        if item is None or rules is None:
            return None
        if item.data(0, ROLE_KIND) != "rule":
            return None
        return rules.get(int(item.data(0, ROLE_ID)))

    def new_rule(self) -> None:
        rules = self.rules
        if rules is None:
            return
        rule = rules.add()
        # a rule you just wrote is one you meant to write
        rules.trusted = True
        if self.edit_rule(rule) is None:
            rules.remove(rule.id)
        self.refresh()

    def edit_rule(self, rule: core.Rule):
        dialog = RuleEditor(rule, self._document, self.host)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return None
        dialog.gather()
        self._document.modified = True
        self.refresh()
        self.changed.emit()
        return rule

    def run_all(self) -> None:
        rules = self.rules
        if rules is None or not len(rules):
            return
        if not rules.trusted:
            self.trust()
        results = rules.run_all(self._document)
        self._report(results)

    def run_one(self, rule: core.Rule) -> None:
        rules = self.rules
        if rules is not None and not rules.trusted:
            self.trust()
        self._report([core.run(rule, self._document)])

    def _report(self, results) -> None:
        failed = [r for r in results if not r.ok]
        if failed:
            self.host.status_message.setStyleSheet("color: %s;" % C.error)
            self.host.status_message.setText(failed[0].summary())
        elif results:
            self.host.status_message.setStyleSheet("")
            self.host.status_message.setText(
                "; ".join(r.summary() for r in results))
        if any(r.changed for r in results):
            self.changed.emit()

    # -- events ------------------------------------------------------------

    def _double_clicked(self, item, _column) -> None:
        rule = self.selected_rule()
        if rule is not None:
            self.edit_rule(rule)

    def _menu(self, pos: QtCore.QPoint) -> None:
        rule = self.selected_rule()
        menu = QtWidgets.QMenu(self)
        if rule is not None:
            menu.addAction("Edit...").triggered.connect(
                lambda: self.edit_rule(rule))
            menu.addAction("Run").triggered.connect(
                lambda: self.run_one(rule))
            toggle = menu.addAction("Enabled")
            toggle.setCheckable(True)
            toggle.setChecked(rule.enabled)
            toggle.triggered.connect(lambda on: self._enable(rule, on))
            menu.addSeparator()
            menu.addAction("Delete").triggered.connect(
                lambda: self._delete(rule))
            menu.addSeparator()
        menu.addAction("New rule").triggered.connect(self.new_rule)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _enable(self, rule: core.Rule, on: bool) -> None:
        rule.enabled = bool(on)
        self._document.modified = True
        self.refresh()

    def _delete(self, rule: core.Rule) -> None:
        rules = self.rules
        if rules is None:
            return
        rules.remove(rule.id)
        self._document.modified = True
        self.refresh()


class FormDialog(QtWidgets.QDialog):
    """A window a rule asked for: controls bound to parameters.

    The model moves while you drag, because a slider you have to let go
    of before anything happens is a slider you cannot judge anything
    with. Rebuilds are coalesced on a short timer so a drag across the
    whole range asks for a handful of rebuilds rather than two hundred.

    Cancel puts every parameter back where it was, so trying something
    costs nothing.
    """

    def __init__(self, host, document, title: str, controls) -> None:
        super().__init__(host)
        self.host = host
        self.document = document
        self.controls = list(controls)
        self.setWindowTitle(title or "dLogic")
        self.setWindowIcon(icons.icon("params", 24))
        self.setMinimumWidth(380)

        # what to put back if this is cancelled
        self._before = {name: document.params[name].expression
                        for name in document.params.names()}

        self._pending = QtCore.QTimer(self)
        self._pending.setSingleShot(True)
        self._pending.setInterval(120)
        self._pending.timeout.connect(self._rebuild)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        form = QtWidgets.QFormLayout()
        form.setSpacing(8)
        self.widgets = {}
        for control in self.controls:
            widget = self._build(control)
            if widget is None:
                continue
            if control.kind == "label":
                form.addRow(widget)
            else:
                form.addRow(control.label, widget)
        layout.addLayout(form)
        layout.addStretch(1)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # -- building ----------------------------------------------------------

    def _value(self, name: str, fallback: float = 0.0) -> float:
        params = self.document.params
        if name in params:
            return float(params[name].value)
        return fallback

    def _build(self, control):
        if control.kind == "label":
            label = QtWidgets.QLabel(control.label)
            label.setWordWrap(True)
            label.setProperty("hint", True)
            return label

        if control.param and control.param not in self.document.params:
            # a form may name a parameter the model has not got yet, and
            # making it is friendlier than refusing to open
            self.document.params.add(control.param,
                                     repr(float(control.low)))
            self._before.pop(control.param, None)

        if control.kind == "slider":
            return self._slider(control)
        if control.kind == "choice":
            return self._choice(control)
        return self._number(control)

    def _slider(self, control):
        row = QtWidgets.QWidget()
        line = QtWidgets.QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)

        step = control.step or 1.0
        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        slider.setMinimum(0)
        slider.setMaximum(max(1, int(round((control.high - control.low)
                                           / step))))
        slider.setValue(int(round(
            (self._value(control.param, control.low) - control.low) / step)))
        readout = QtWidgets.QLabel("")
        readout.setMinimumWidth(56)
        readout.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        def moved(ticks: int) -> None:
            value = control.low + ticks * step
            readout.setText(("%g" % value))
            self._set(control.param, value)

        slider.valueChanged.connect(moved)
        moved(slider.value())
        line.addWidget(slider, 1)
        line.addWidget(readout)
        self.widgets[control.param] = slider
        return row

    def _number(self, control):
        box = QtWidgets.QDoubleSpinBox()
        box.setRange(-1e6, 1e6)
        box.setDecimals(3)
        box.setValue(self._value(control.param))
        box.valueChanged.connect(
            lambda v, p=control.param: self._set(p, v))
        self.widgets[control.param] = box
        return box

    def _choice(self, control):
        combo = QtWidgets.QComboBox()
        combo.addItems(control.options)
        combo.currentTextChanged.connect(
            lambda text, p=control.param: self._set_text(p, text))
        self.widgets[control.param] = combo
        return combo

    # -- driving the model -------------------------------------------------

    def _set(self, name: str, value: float) -> None:
        if not name:
            return
        self.document.params.set_expression(name, repr(float(value)))
        self.document.modified = True
        self._pending.start()

    def _set_text(self, name: str, text: str) -> None:
        """A choice that names a material sets the material, not a number."""
        if name == "material" and hasattr(self.document, "material"):
            self.document.material = text
            self.document.modified = True
            self._pending.start()
            return
        try:
            self._set(name, float(text))
        except ValueError:
            pass

    def _rebuild(self) -> None:
        try:
            self.host.rebuild(keep_camera=True)
        except Exception:
            pass

    def reject(self) -> None:
        for name, expression in self._before.items():
            if name in self.document.params:
                self.document.params.set_expression(name, expression)
        self._rebuild()
        super().reject()


def open_form(host, document, title: str, controls) -> bool:
    """What core.rules calls when a rule asks for a window."""
    dialog = FormDialog(host, document, title, controls)
    return dialog.exec() == QtWidgets.QDialog.Accepted
