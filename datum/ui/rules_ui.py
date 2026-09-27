"""dLogic: the panel of rules, and the windows you write scripts in.

Laid out like Inventor's iLogic browser because that is what people will
be looking for: the document at the top, its rules under it, double-click
to open one. What is different is the line across the top saying the code
has not been allowed to run, because a document carrying code is a
document that can do something to your machine, and the file is not the
one who gets to decide that.

The editor is the same for a rule and for a code feature, and it is built
around one idea: nobody should have to remember a name. Everything the
script can reach is listed down the side, with the model's own
parameters, dimensions, features and components at the top, and a
double-click puts it in. A script that fails has its line lit up.
"""

from __future__ import annotations

import keyword
import math
import os
import re
from typing import Callable, List, Optional, Sequence, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ..core import ahead as core_ahead
from ..core import geometry
from ..core import rules as core
from ..core.features import CodeFeature
from . import dialogs, icons
from .theme import C

ROLE_ID = QtCore.Qt.UserRole + 1
ROLE_KIND = QtCore.Qt.UserRole + 2
ROLE_INSERT = QtCore.Qt.UserRole + 3
ROLE_HELP = QtCore.Qt.UserRole + 4

# What a new rule starts as.  It is the documentation most people will
# ever read, so it shows the things worth knowing in the order they are
# worth knowing them.  The panel on the right has everything else, and
# docs/DLOGIC.md has the long version.
STARTER = '''"""What this rule does."""

# Read a parameter, and set one to drive the model:
#     params.width = params.height * 2
#
# Ask, with sliders that move the model as you drag:
#     form("Size",
#          slider("width", 20, 200, 5),
#          slider("height", 10, 120, 5))
#
# Everything this document has, and every command, is listed on the
# right: double-click one to put it in.  Run with F5 or Ctrl+Enter.

log("parameters:", ", ".join(params.names()) or "none yet")
'''

# What a new code feature starts as: something that builds, so the first
# thing anybody sees is a solid appearing, and then they change it.
CODE_STARTER = '''"""What this builds."""

# Build a solid and hand it back with result(...).  Parameters are read
# with params.name; every shape and operation is listed on the right.
# Build with F5 or Ctrl+Enter.

post = cylinder(10, 120)                     # radius, height
row = post.repeat(5, x=60)                   # five of them, 60 apart
plate = box(300, 40, 10).move(-30, -20, -10)

result(plate + row)
'''


# ---------------------------------------------------------------- reference
#
# (label, text to insert, what it does).  A label ending in "..." inserts
# something to fill in.

Entry = Tuple[str, str, str]
Group = Tuple[str, List[Entry]]

RULE_COMMANDS: List[Group] = [
    ("Ask with a window", [
        ("form(...)",
         'form("Size",\n'
         '     note("Drag to size it."),\n'
         '     slider("width", 20, 200, 5),\n'
         '     number("depth"),\n'
         '     choice("material", ["Steel, Mild", "Aluminium 6061"]))\n',
         "Open a window whose controls drive parameters.  The model moves "
         "while you drag; Cancel puts everything back.  True if OK was "
         "pressed."),
        ("slider(name, low, high, step)", 'slider("width", 10, 200, 5)',
         "A parameter you drag."),
        ("number(name)", 'number("depth")', "A parameter you type."),
        ("choice(name, options)", 'choice("size", ["10", "20", "30"])',
         "Pick from a list.  Named material, it sets the material."),
        ("note(text)", 'note("What this form is for.")',
         "A line of explanation on the form."),
    ]),
    ("Drive the model", [
        ("Set a parameter", "params.width = 100\n",
         "Assigning to a parameter sets it, and the model rebuilds."),
        ("Set to an expression", 'params.width = "height * 2"\n',
         "A string is kept as an expression, so it follows from then on."),
        ("Create if missing", 'if "gap" not in params:\n    params.gap = 2\n',
         "A rule can bring its own parameters with it."),
        ("Set a sketch dimension", 'sketches["Sketch1"].dims.d1 = 40\n',
         "Dimensions are d1, d2... per sketch.  dims.d1 works too while "
         "only one sketch has a d1."),
        ("Suppress a feature", 'features["Fillet1"].suppressed = True\n',
         "Turn a feature off, or back on with False."),
        ("Suppress when...", 'features.suppress("Fillet1", '
         "params.width < 50)\n",
         "Suppressed while the condition holds, back when it does not."),
        ("Set the material", 'material("Steel, Mild")\n',
         "Any name from the material library.  material() reads it."),
        ("Set the appearance", 'appearance("Paint, Machine Yellow")\n',
         "How it looks, independent of what it is made of."),
        ("Set a property", 'props.PartNumber = "BR-%d" % params.width\n',
         "Title, PartNumber, Designer, Description, or any name you like."),
    ]),
    ("Assemblies", [
        ("Move a component", 'components["Bolt:1"].move(10, 0, 0)\n',
         "Shift it from where it is."),
        ("Place a component",
         'components["Bolt:1"].place(0, 0, 50, rz=90)\n',
         "Put it at a position, turned rx, ry, rz degrees."),
        ("Suppress a component", 'components["Bolt:1"].suppressed = True\n',
         "Take it out of the assembly without deleting it."),
        ("Hide a component", 'components["Bolt:1"].visible = False\n',
         "Still there, not drawn."),
        ("Every one like...",
         'for c in components.like("Bolt"):\n'
         '    c.visible = params.show_bolts > 0\n',
         "Every component whose name contains the text."),
        ("Count them", 'log(len(components), "components")\n',
         "components is a list you can loop over."),
    ]),
    ("Say something", [
        ("log(...)", 'log("width is", params.width)\n',
         "Shown under the editor, and in the status bar."),
        ("Round a number", "round(params.width, 1)", ""),
        ("Format a number", '"%.1f mm" % params.width', ""),
    ]),
]

MATHS: Group = ("Maths", [
    ("math.pi", "math.pi", ""),
    ("math.sqrt(x)", "math.sqrt()", ""),
    ("math.sin / cos / tan", "math.sin(math.radians())", "Angles in "
     "radians: math.radians turns degrees into them."),
    ("math.atan2(y, x)", "math.degrees(math.atan2(y, x))", ""),
    ("math.ceil(x)", "math.ceil()", "Round up to a whole number."),
    ("math.floor(x)", "math.floor()", "Round down to a whole number."),
    ("math.hypot(x, y)", "math.hypot()", "Length of a diagonal."),
    ("min / max", "max(a, b)", ""),
    ("range(n)", "for i in range(n):\n    ", "0, 1, ... n-1"),
])

CODE_TOOLKIT: List[Group] = [
    ("Answer", [
        ("result(solid)", "result(body)\n",
         "Hand back what this feature builds.  Or leave it in a variable "
         "called body."),
        ("log(...)", 'log("built", n, "posts")\n',
         "Shown under the editor when it builds."),
    ]),
    ("Solids", [
        ("box(x, y, z)", "box(100, 50, 20)",
         "From the origin along +X +Y +Z."),
        ("box(..., centered=True)", "box(100, 50, 20, centered=True)",
         "Centred on the origin."),
        ("cylinder(radius, height)", "cylinder(10, 100)",
         "Standing on the origin, along Z.  axis=\"x\" to lay it down."),
        ("cone(bottom, top, height)", "cone(20, 5, 50)", ""),
        ("sphere(radius)", "sphere(25)", ""),
        ("torus(ring, tube)", "torus(50, 8)", ""),
        ("tube(outer, inner, height)", "tube(20, 16, 200)", "A pipe."),
        ("rod(start, end, radius)", "rod((0, 0, 0), (0, 0, 900), 10)",
         "A round bar between two points: balusters, pins, braces."),
        ("beam(start, end, width, height)",
         "beam((0, 0, 0), (2000, 0, 1000), 40, 200)",
         "A square bar between two points, standing upright."),
    ]),
    ("Profiles", [
        ("circle(radius)", "circle(20)", "Flat, on XY, about the origin."),
        ("rect(width, height)", "rect(40, 20)", ""),
        ("regular(sides, radius)", "regular(6, 15)", "A hexagon, and so on."),
        ("polygon(points)", "polygon([(0, 0), (60, 0), (60, 10), (0, 40)])",
         "Any outline, as corner points."),
        ("profile.extrude(height)", ".extrude(30)", "Up along Z."),
        ("profile.extrude(h, taper)", ".extrude(30, taper=5)",
         "Drafted, in degrees."),
        ("profile.revolve(degrees)", '.revolve(360, axis="y")',
         "Turned about an axis in its own plane."),
        ("profile.sweep(path)", ".sweep(path([(0, 0, 0), (0, 0, 500)]))",
         "Moved along a path, kept square to it."),
        ("profile.move(x, y)", ".move(10, 0)", "Off-centre, before use."),
    ]),
    ("Paths", [
        ("path(points)", "path([(0, 0, 0), (1000, 0, 500), (1400, 0, 500)])",
         "Straight lines through the points."),
        ("path(points, corner=r)",
         "path([(0, 0, 0), (1000, 0, 500), (1400, 0, 500)], corner=80)",
         "Rounded at every bend."),
        ("arc(start, middle, end)", "arc((0, 0, 0), (50, 50, 0), (100, 0, 0))",
         "Through three points."),
        ("helix(radius, pitch, height)", "helix(40, 12, 120)",
         "A coil round Z: springs and threads."),
    ]),
    ("Move and copy", [
        (".move(x, y, z)", ".move(0, 0, 100)",
         "A moved copy.  The original stays, so one solid can be reused."),
        (".move_to(x, y, z)", ".move_to(0, 0, 0)", "Its centre, put there."),
        (".rotate(axis, degrees)", '.rotate("z", 45)',
         "About the origin, or about=(x, y, z)."),
        (".mirror(plane)", '.mirror("yz")', "xy, yz or xz."),
        (".scale(factor)", ".scale(2)", ""),
        (".repeat(n, x, y, z)", ".repeat(10, x=100)",
         "n copies, each one step further on."),
        (".repeat_around(n, axis)", '.repeat_around(6, "z")',
         "n copies round an axis: bolt circles, spokes."),
    ]),
    ("Combine", [
        ("a + b", "a + b", "Joined."),
        ("a - b", "a - b", "b taken out of a."),
        ("a & b", "a & b", "Only what they share."),
        ("union(list)", "union(parts)",
         "Everything in a list as one solid.  Much faster than adding "
         "them one at a time."),
        (".fillet(radius)", ".fillet(2)", "Every edge rounded."),
        (".chamfer(distance)", ".chamfer(1)", "Every edge bevelled."),
    ]),
    ("Measure", [
        (".volume", ".volume", "mm3"),
        (".size", ".size", "(x, y, z) of its bounding box."),
        (".bbox", ".bbox", "(xmin, ymin, zmin, xmax, ymax, zmax)"),
        (".centre", ".centre", "Middle of its bounding box."),
    ]),
    ("Examples", [
        ("Row of posts on a plate",
         "post = cylinder(10, 120)\n"
         "row = post.repeat(int(params.n), x=60)\n"
         "result(box(60 * params.n, 40, 10).move(-30, -20, -10) + row)\n",
         "Change n and the number of posts follows."),
        ("Flange with a bolt circle",
         "disc = cylinder(60, 12)\n"
         "hole = cylinder(5, 12).move(45, 0, 0)\n"
         "result(disc - hole.repeat_around(8, \"z\") - cylinder(20, 12))\n",
         ""),
        ("Handrail along a stair",
         "run, rise = 3000, 1800\n"
         "rail = circle(25).sweep(path([(0, 0, 900), (run, 0, 900 + rise),\n"
         "                              (run + 400, 0, 900 + rise)],\n"
         "                             corner=120))\n"
         "posts = [rod((x, 0, x * rise / run), (x, 0, 900 + x * rise / run),"
         " 10)\n"
         "         for x in range(150, run, 150)]\n"
         "result(union(posts) + rail)\n",
         "The stair sample does this properly: examples/railing."),
        ("Spring",
         "result(circle(3).sweep(helix(20, 10, 80)))\n", ""),
    ]),
]


def _values(document) -> List[Entry]:
    out = []
    params = getattr(document, "params", None)
    if params is None:
        return out
    for name in params.names():
        try:
            value = "%g" % params[name].value
        except (TypeError, ValueError):
            value = "?"
        out.append(("%s = %s" % (name, value), "params.%s" % name,
                    params[name].expression))
    return out


def _sketch_dims(document) -> List[Entry]:
    out = []
    for feature in getattr(document, "features", ()) or ():
        sketch = getattr(feature, "sketch", None)
        if sketch is None or not hasattr(sketch, "constraints"):
            continue
        for c in sketch.constraints.values():
            if c.name and c.is_dimension:
                out.append(("%s.%s = %g" % (feature.name, c.name, c.value),
                            'sketches["%s"].dims.%s' % (feature.name, c.name),
                            c.expression or "a %s dimension" % c.kind))
    return out


def _props_key(key: str) -> str:
    return ("props.%s" % key if key.isidentifier()
            else 'props["%s"]' % key)


def rule_groups(document) -> List[Group]:
    """What a rule can reach in this document, then every command."""
    groups: List[Group] = [("Parameters", _values(document))]
    dims = _sketch_dims(document)
    if dims:
        groups.append(("Sketch dimensions", dims))
    feats = [(f.name, 'features["%s"]' % f.name, f.type_name)
             for f in getattr(document, "features", ()) or ()
             if hasattr(f, "suppressed")]
    if feats:
        groups.append(("Features", feats))
    occ = getattr(document, "occurrences", None)
    if occ:
        groups.append(("Components", [
            (o.name, 'components["%s"]' % o.name, o.ref.path)
            for o in occ]))
    props = getattr(document, "properties", None) or {}
    keys = sorted(set(props) | {"Title", "PartNumber", "Designer",
                                "Description"})
    groups.append(("Properties", [
        ("%s = %s" % (k, props.get(k, "")), _props_key(k), "")
        for k in keys]))
    groups += RULE_COMMANDS
    try:
        from ..core import materials
        names = sorted(materials.library().materials)
        groups.append(("Materials", [(n, 'material("%s")' % n, "")
                                     for n in names]))
    except Exception:
        pass
    groups.append(MATHS)
    return groups


def code_groups(document) -> List[Group]:
    """What a code feature can use: parameters, then the toolkit."""
    return [("Parameters (read only)", _values(document))] + \
        CODE_TOOLKIT + [MATHS]


RULE_NAMES = ["params", "dims", "sketches", "features", "components",
              "props", "material", "appearance", "form", "slider", "number",
              "choice", "note", "log", "doc", "math", "units"]
CODE_NAMES = sorted(set(geometry.namespace()) - {"math"}) + [
    "params", "log", "result", "math"]
SOLID_WORDS = ["move", "move_to", "rotate", "mirror", "scale", "fillet",
               "chamfer", "repeat", "repeat_around", "fuse", "cut", "common",
               "volume", "size", "bbox", "centre", "extrude", "revolve",
               "sweep"]
COMPONENT_WORDS = ["move", "place", "suppressed", "visible", "grounded",
                   "name", "position", "path"]


def completion_words(document, mode: str, owner: Optional[str]) -> List[str]:
    """Names worth offering after ``owner.``, or at the top level."""
    if owner is None:
        return (RULE_NAMES if mode == "rule" else CODE_NAMES) + \
            ["for", "in", "range", "if", "else", "elif", "round", "int",
             "len", "min", "max", "True", "False"]
    params = getattr(document, "params", None)
    if owner == "params":
        return (list(params.names()) if params is not None else []) + (
            ["names"] if mode == "code" else ["names", "expression"])
    if owner == "dims":
        return [c for _l, _i, _h in _sketch_dims(document)
                for c in [_i.rsplit(".", 1)[-1]]]
    if owner == "props":
        return sorted(set(getattr(document, "properties", {}) or {})
                      | {"Title", "PartNumber", "Designer", "Description"})
    if owner == "math":
        return [n for n in dir(math) if not n.startswith("_")]
    if owner == "features":
        return ["suppress", "names"]
    if owner == "components":
        return ["like", "names"]
    if owner == "sketches":
        return ["names", "all"]
    return SOLID_WORDS if mode == "code" else COMPONENT_WORDS + [
        "suppressed", "dims"]


# ------------------------------------------------------------------ editor


def _format(colour: str, bold: bool = False,
            italic: bool = False) -> QtGui.QTextCharFormat:
    f = QtGui.QTextCharFormat()
    f.setForeground(QtGui.QColor(colour))
    if bold:
        f.setFontWeight(QtGui.QFont.Bold)
    if italic:
        f.setFontItalic(True)
    return f


class PythonHighlighter(QtGui.QSyntaxHighlighter):
    """Just enough colour to read a script by: words, strings, comments."""

    def __init__(self, document: QtGui.QTextDocument,
                 names: Sequence[str]) -> None:
        super().__init__(document)
        ours = QtGui.QColor(C.sketch_picked).lighter(150).name()
        self.rules = [
            (re.compile(r"\b(%s)\b" % "|".join(keyword.kwlist)),
             _format(C.sketch_free, bold=True)),
            (re.compile(r"\b(%s)\b" % "|".join(sorted(set(names)))),
             _format(ours)),
            (re.compile(r"\b\d+(\.\d*)?([eE][-+]?\d+)?\b"),
             _format(C.warn)),
        ]
        self.string = _format(C.sketch_dim)
        self.comment = _format(C.text_dim, italic=True)

    def highlightBlock(self, text: str) -> None:
        for pattern, fmt in self.rules:
            for m in pattern.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)
        self.setCurrentBlockState(0)
        n = len(text)
        i = 0
        state = self.previousBlockState()
        if state in (1, 2):
            quote = "'''" if state == 1 else '"""'
            end = text.find(quote)
            if end < 0:
                self.setFormat(0, n, self.string)
                self.setCurrentBlockState(state)
                return
            self.setFormat(0, end + 3, self.string)
            i = end + 3
        while i < n:
            c = text[i]
            if c == "#":
                self.setFormat(i, n - i, self.comment)
                return
            if c in "'\"":
                triple = text[i:i + 3]
                if triple in ("'''", '"""'):
                    end = text.find(triple, i + 3)
                    if end < 0:
                        self.setFormat(i, n - i, self.string)
                        self.setCurrentBlockState(1 if triple == "'''" else 2)
                        return
                    self.setFormat(i, end + 3 - i, self.string)
                    i = end + 3
                    continue
                j = i + 1
                while j < n and text[j] != c:
                    j += 2 if text[j] == "\\" else 1
                self.setFormat(i, min(j + 1, n) - i, self.string)
                i = j + 1
                continue
            i += 1


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
    """Enough of an editor to write a script in.

    Numbers down the side, four-space tabs, indentation kept on Return and
    added after a colon, a failing line lit up, and names offered after a
    dot or on Ctrl+Space.
    """

    run_requested = QtCore.Signal()

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

        self.error_line = 0
        self.textChanged.connect(self._text_changed)

        # names to offer: set by whoever knows what the script can see
        self.words: Callable[[Optional[str]], List[str]] = lambda owner: []
        self._model = QtCore.QStringListModel(self)
        self.completer = QtWidgets.QCompleter(self._model, self)
        self.completer.setWidget(self)
        self.completer.setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
        self.completer.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
        self.completer.activated.connect(self._complete)

    # -- the margin ---------------------------------------------------------

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
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible():
                failing = number + 1 == self.error_line
                painter.setPen(QtGui.QColor(C.error if failing
                                            else C.text_dim))
                painter.drawText(
                    0, int(top), widget.width() - 6,
                    int(self.blockBoundingRect(block).height()),
                    QtCore.Qt.AlignRight, str(number + 1))
            top += self.blockBoundingRect(block).height()
            block = block.next()
            number += 1
        painter.end()

    # -- the failing line ---------------------------------------------------

    def mark_error(self, line: int) -> None:
        """Light up a line, and go to it.  Zero clears the mark."""
        self.error_line = int(line or 0)
        if not self.error_line:
            self.setExtraSelections([])
            self.gutter.update()
            return
        block = self.document().findBlockByNumber(self.error_line - 1)
        if not block.isValid():
            self.error_line = 0
            self.setExtraSelections([])
            return
        mark = QtWidgets.QTextEdit.ExtraSelection()
        fmt = QtGui.QTextCharFormat()
        colour = QtGui.QColor(C.error)
        colour.setAlpha(70)
        fmt.setBackground(colour)
        fmt.setProperty(QtGui.QTextFormat.FullWidthSelection, True)
        mark.format = fmt
        cursor = QtGui.QTextCursor(block)
        mark.cursor = cursor
        self.setExtraSelections([mark])
        self.setTextCursor(cursor)
        self.centerCursor()
        self.gutter.update()

    def _text_changed(self) -> None:
        if self.error_line:
            self.mark_error(0)

    # -- typing -------------------------------------------------------------

    def insert_snippet(self, text: str) -> None:
        """Put text in at the cursor, indented to match the line it lands on."""
        cursor = self.textCursor()
        line = cursor.block().text()
        indent = line[:len(line) - len(line.lstrip(" "))]
        if "\n" in text.rstrip("\n"):
            text = text.replace("\n", "\n" + indent)
            if text.endswith("\n" + indent):
                text = text[:-len(indent)] if indent else text
        cursor.insertText(text)
        self.setTextCursor(cursor)
        self.setFocus()

    def _context(self) -> Tuple[Optional[str], str]:
        cursor = self.textCursor()
        before = cursor.block().text()[:cursor.positionInBlock()]
        m = re.search(r"(?:([A-Za-z_][A-Za-z_0-9]*)\s*\.)?"
                      r"([A-Za-z_][A-Za-z_0-9]*)?$", before)
        if m is None:
            return None, ""
        owner = m.group(1)
        if owner is None and before.rstrip().endswith(")."):
            owner = "?"
        elif owner is None and re.search(r"[\])\"']\.[A-Za-z_0-9]*$", before):
            owner = "?"
        return owner, m.group(2) or ""

    def show_completion(self) -> None:
        owner, prefix = self._context()
        words = sorted(set(self.words(owner)), key=str.lower)
        if not words:
            self.completer.popup().hide()
            return
        self._model.setStringList(words)
        self.completer.setCompletionPrefix(prefix)
        if self.completer.completionCount() == 0:
            self.completer.popup().hide()
            return
        popup = self.completer.popup()
        popup.setCurrentIndex(self.completer.completionModel().index(0, 0))
        rect = self.cursorRect()
        rect.setWidth(max(160, popup.sizeHintForColumn(0)
                          + popup.verticalScrollBar().sizeHint().width()))
        self.completer.complete(rect)

    def _complete(self, chosen) -> None:
        if isinstance(chosen, QtCore.QModelIndex):
            chosen = chosen.data()
        _owner, prefix = self._context()
        cursor = self.textCursor()
        for _ in prefix:
            cursor.deletePreviousChar()
        cursor.insertText(str(chosen))
        self.setTextCursor(cursor)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        key = event.key()
        popup = self.completer.popup()
        if popup.isVisible() and key in (
                QtCore.Qt.Key_Enter, QtCore.Qt.Key_Return,
                QtCore.Qt.Key_Escape, QtCore.Qt.Key_Tab,
                QtCore.Qt.Key_Backtab):
            event.ignore()
            return
        ctrl = bool(event.modifiers() & QtCore.Qt.ControlModifier)
        if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter) and ctrl:
            self.run_requested.emit()
            return
        if key == QtCore.Qt.Key_F5:
            self.run_requested.emit()
            return
        if ctrl and key == QtCore.Qt.Key_Space:
            self.show_completion()
            return
        if key == QtCore.Qt.Key_Tab:
            self.insertPlainText("    ")
            return
        if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            cursor = self.textCursor()
            line = cursor.block().text()
            indent = len(line) - len(line.lstrip(" "))
            if line[:cursor.positionInBlock()].rstrip().endswith(":"):
                indent += 4
            super().keyPressEvent(event)
            self.insertPlainText(" " * indent)
            return
        if key == QtCore.Qt.Key_Backspace and not \
                self.textCursor().hasSelection():
            cursor = self.textCursor()
            before = cursor.block().text()[:cursor.positionInBlock()]
            if before and not before.strip():
                for _ in range(len(before) % 4 or 4):
                    cursor.deletePreviousChar()
                return
        super().keyPressEvent(event)
        if event.text() == ".":
            self.show_completion()
        elif popup.isVisible():
            if event.text() and not (event.text().isalnum()
                                     or event.text() == "_") \
                    and key != QtCore.Qt.Key_Backspace:
                popup.hide()
            else:
                self.show_completion()


class ReferencePanel(QtWidgets.QWidget):
    """Everything a script can use, and a double-click to put it in."""

    insert = QtCore.Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Find a name or command")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(12)
        self.tree.itemDoubleClicked.connect(self._chosen)
        self.tree.itemActivated.connect(self._chosen)
        self.tree.currentItemChanged.connect(self._explain)
        layout.addWidget(self.tree, 1)
        self.help = QtWidgets.QLabel(
            "Double-click to put it in the script.")
        self.help.setWordWrap(True)
        self.help.setProperty("hint", True)
        self.help.setMinimumHeight(48)
        self.help.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        layout.addWidget(self.help)

    def set_groups(self, groups: Sequence[Group],
                   collapsed: Sequence[str] = ("Materials", "Maths")) -> None:
        self.tree.clear()
        for title, entries in groups:
            head = QtWidgets.QTreeWidgetItem(self.tree, [title])
            font = head.font(0)
            font.setBold(True)
            head.setFont(0, font)
            head.setFlags(head.flags() & ~QtCore.Qt.ItemIsSelectable)
            if not entries:
                empty = QtWidgets.QTreeWidgetItem(head, ["(none yet)"])
                empty.setForeground(0, QtGui.QBrush(QtGui.QColor(
                    C.text_dim)))
                empty.setFlags(QtCore.Qt.ItemIsEnabled)
            for label, text, hint in entries:
                item = QtWidgets.QTreeWidgetItem(head, [label])
                item.setData(0, ROLE_INSERT, text)
                item.setData(0, ROLE_HELP, hint)
                item.setToolTip(0, hint or text)
            head.setExpanded(title not in collapsed)
        self._filter(self.search.text())

    def _filter(self, text: str) -> None:
        needle = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            head = self.tree.topLevelItem(i)
            shown = 0
            for j in range(head.childCount()):
                child = head.child(j)
                hit = (not needle or needle in child.text(0).lower()
                       or needle in str(child.data(0, ROLE_INSERT)
                                        or "").lower())
                child.setHidden(not hit)
                shown += hit
            head.setHidden(bool(needle) and not shown)
            if needle and shown:
                head.setExpanded(True)

    def _chosen(self, item, _column=0) -> None:
        text = item.data(0, ROLE_INSERT)
        if text:
            self.insert.emit(str(text))

    def _explain(self, item, _previous=None) -> None:
        if item is None or not item.data(0, ROLE_INSERT):
            self.help.setText("Double-click to put it in the script.")
            return
        hint = item.data(0, ROLE_HELP) or ""
        text = str(item.data(0, ROLE_INSERT)).strip().splitlines()[0]
        self.help.setText(("%s\n%s" % (text, hint)).strip())


class ScriptEditor(QtWidgets.QWidget):
    """The editor, its output, and the reference down the side.

    One of these is in a rule's window and one in a code feature's, with
    the side listing what that kind of script can use.
    """

    run_requested = QtCore.Signal()

    def __init__(self, document, mode: str, source: str,
                 parent=None) -> None:
        super().__init__(parent)
        self.document = document
        self.mode = mode

        self.editor = CodeEditor()
        self.editor.setPlainText(source)
        self.editor.words = lambda owner: completion_words(
            document, mode, owner)
        self.highlighter = PythonHighlighter(
            self.editor.document(),
            RULE_NAMES if mode == "rule" else CODE_NAMES)
        self.editor.run_requested.connect(self.run_requested)

        self.output = QtWidgets.QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText(
            "Run it (F5 or Ctrl+Enter) and whatever it logs appears here.")

        self.reference = ReferencePanel()
        self.reference.insert.connect(self.editor.insert_snippet)
        self.refresh_reference()

        left = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        left.addWidget(self.editor)
        left.addWidget(self.output)
        left.setStretchFactor(0, 4)
        left.setStretchFactor(1, 1)
        left.setSizes([420, 110])

        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(self.reference)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([620, 270])

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(split)

    def refresh_reference(self) -> None:
        self.reference.set_groups(rule_groups(self.document)
                                  if self.mode == "rule"
                                  else code_groups(self.document))

    def text(self) -> str:
        return self.editor.toPlainText()

    def show_result(self, text: str, ok: bool, line: int = 0) -> None:
        self.output.setPlainText(text or "(nothing to say)")
        self.output.setStyleSheet("color: %s;" % (C.text if ok else C.error))
        self.editor.mark_error(0 if ok else line)


def _line_of(error: str) -> int:
    m = re.search(r"\bline (\d+)", error or "")
    return int(m.group(1)) if m else 0


class RuleEditor(QtWidgets.QDialog):
    """Write a rule, say when it runs, run it, see what it said."""

    model_changed = QtCore.Signal()

    def __init__(self, rule: core.Rule, document, parent=None) -> None:
        super().__init__(parent)
        self.rule = rule
        self.document = document
        self.setWindowTitle("dLogic rule")
        self.setWindowIcon(icons.icon("auto", 24))
        self.resize(980, 680)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("Name"))
        self.name_edit = QtWidgets.QLineEdit(rule.name)
        top.addWidget(self.name_edit, 1)
        self.button_box = QtWidgets.QCheckBox("Show as a button")
        self.button_box.setToolTip(
            "Pin this rule to the top of the dLogic panel, one click away. "
            "Made for rules that open a form.")
        self.button_box.setChecked(rule.button)
        top.addWidget(self.button_box)
        layout.addLayout(top)

        self.script = ScriptEditor(document, "rule", rule.source or STARTER)
        self.script.run_requested.connect(self.run_now)
        layout.addWidget(self.script, 1)

        when = QtWidgets.QHBoxLayout()
        when.setSpacing(12)
        when.addWidget(QtWidgets.QLabel("Runs"))
        self.on_rebuild = QtWidgets.QCheckBox("after every rebuild")
        self.on_rebuild.setChecked(rule.on_rebuild)
        self.on_open = QtWidgets.QCheckBox("when the document opens")
        self.on_open.setChecked(rule.on_open)
        self.before_save = QtWidgets.QCheckBox("before saving")
        self.before_save.setChecked(rule.before_save)
        for box in (self.on_rebuild, self.on_open, self.before_save):
            when.addWidget(box)
        when.addWidget(QtWidgets.QLabel("when these change:"))
        self.watch = QtWidgets.QLineEdit(", ".join(rule.watch))
        self.watch.setPlaceholderText("width, height")
        self.watch.setToolTip(
            "Parameters that set this rule off when their value changes, "
            "separated by commas.")
        names = QtWidgets.QCompleter(
            list(getattr(document, "params", None).names())
            if getattr(document, "params", None) is not None else [], self)
        self.watch.setCompleter(names)
        when.addWidget(self.watch, 1)
        layout.addLayout(when)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        run = QtWidgets.QPushButton("Run")
        run.setIcon(icons.icon("update", 16))
        run.setToolTip("Run it now (F5 or Ctrl+Enter)")
        run.clicked.connect(self.run_now)
        buttons.addButton(run, QtWidgets.QDialogButtonBox.ActionRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        QtGui.QShortcut(QtGui.QKeySequence("F5"), self, self.run_now)

    # kept for anything that still reaches for the bare editor
    @property
    def editor(self) -> CodeEditor:
        return self.script.editor

    @property
    def output(self) -> QtWidgets.QPlainTextEdit:
        return self.script.output

    def gather(self) -> core.Rule:
        self.rule.name = self.name_edit.text().strip() or self.rule.name
        self.rule.source = self.script.text()
        self.rule.on_rebuild = self.on_rebuild.isChecked()
        self.rule.on_open = self.on_open.isChecked()
        self.rule.before_save = self.before_save.isChecked()
        self.rule.button = self.button_box.isChecked()
        self.rule.watch = [w.strip() for w in self.watch.text().split(",")
                           if w.strip()]
        return self.rule

    def run_now(self) -> None:
        """Run what is in the box, whether or not it has been saved."""
        rule = core.Rule(name=self.name_edit.text().strip() or "Rule",
                         source=self.script.text())
        result = core.run(rule, self.document)
        text = result.output
        if not result.ok:
            where = "line %d: " % result.line if result.line else ""
            text = (text + "\n" if text else "") + where + result.error
        elif result.changed:
            text = (text + "\n" if text else "") + \
                "set " + ", ".join(result.changed)
        self.script.show_result(text, result.ok, result.line)
        if result.changed:
            self.model_changed.emit()
            self.script.refresh_reference()


class RulesPanel(QtWidgets.QWidget):
    """The dLogic dock: this document, its forms, rules and code."""

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
        column = QtWidgets.QVBoxLayout(self.banner)
        column.setContentsMargins(8, 5, 8, 6)
        column.setSpacing(4)
        self.banner_text = QtWidgets.QLabel("")
        self.banner_text.setWordWrap(True)
        self.banner_text.setStyleSheet("color: %s; font-size: 11px;" % C.warn)
        column.addWidget(self.banner_text)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(6)
        self.allow = QtWidgets.QPushButton("Allow")
        self.allow.setToolTip("Let this document's code run, for this "
                              "session.  Parts it places are allowed too.")
        self.allow.setFixedHeight(22)
        self.allow.clicked.connect(self.trust)
        row.addWidget(self.allow)
        self.allow_folder = QtWidgets.QPushButton("Always for this folder")
        self.allow_folder.setToolTip(
            "Let everything in this document's folder run its code, from "
            "now on.  Kept in your preferences, never in the file.")
        self.allow_folder.setFixedHeight(22)
        self.allow_folder.clicked.connect(self.trust_folder)
        row.addWidget(self.allow_folder)
        row.addStretch(1)
        column.addLayout(row)
        layout.addWidget(self.banner)

        # rules marked as buttons: forms that stay, one click away
        self.forms = QtWidgets.QWidget(self)
        self.forms_layout = QtWidgets.QVBoxLayout(self.forms)
        self.forms_layout.setContentsMargins(6, 6, 6, 4)
        self.forms_layout.setSpacing(3)
        layout.addWidget(self.forms)

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
        self.new_button = QtWidgets.QPushButton("New rule")
        self.code_button = QtWidgets.QPushButton("Code feature")
        self.code_button.setToolTip(
            "A feature whose solid a script builds: stairs, railings, "
            "patterns no dialog can describe")
        self.run_button = QtWidgets.QPushButton("Run all")
        for button, slot in ((self.new_button, self.new_rule),
                             (self.code_button, self.new_code),
                             (self.run_button, self.run_all)):
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

    def code_features(self) -> List[CodeFeature]:
        return [f for f in getattr(self._document, "features", ()) or ()
                if isinstance(f, CodeFeature)]

    @staticmethod
    def _marks(rule: core.Rule) -> List[str]:
        if not rule.enabled:
            return ["off"]
        marks = []
        if rule.on_rebuild:
            marks.append("after rebuild")
        if rule.on_open:
            marks.append("on open")
        if rule.before_save:
            marks.append("before save")
        if rule.watch:
            marks.append("when %s change%s" % (
                ", ".join(rule.watch), "s" if len(rule.watch) == 1 else ""))
        if rule.button:
            marks.append("button")
        return marks or ["when asked"]

    def refresh(self) -> None:
        self.tree.clear()
        while self.forms_layout.count():
            item = self.forms_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        rules = self.rules
        self.setEnabled(self._document is not None)
        self.code_button.setEnabled(hasattr(self._document, "features"))
        if rules is None:
            self.banner.hide()
            self.forms.hide()
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
            item.setIcon(0, icons.icon("auto", 16))
            item.setData(0, ROLE_ID, rule.id)
            item.setData(0, ROLE_KIND, "rule")
            marks = self._marks(rule)
            item.setText(0, "%s   (%s)" % (rule.name, ", ".join(marks)))
            item.setToolTip(0, (rule.source.strip().splitlines() or [""])[0])
            if not rule.enabled:
                item.setForeground(0, QtGui.QBrush(QtGui.QColor(C.text_dim)))

        code = self.code_features()
        for feature in code:
            item = QtWidgets.QTreeWidgetItem(root, [feature.name])
            item.setIcon(0, icons.icon("code", 16))
            item.setData(0, ROLE_ID, feature.id)
            item.setData(0, ROLE_KIND, "code")
            item.setText(0, "%s   (code feature)" % feature.name)
            if feature.error:
                item.setForeground(0, QtGui.QBrush(QtGui.QColor(C.error)))
                item.setToolTip(0, feature.error)
        self.tree.expandAll()

        buttons = rules.buttons
        self.forms.setVisible(bool(buttons))
        for rule in buttons:
            button = QtWidgets.QPushButton(rule.name)
            button.setIcon(icons.icon("auto", 16))
            button.setFixedHeight(26)
            button.setStyleSheet("text-align: left; padding-left: 8px;")
            button.setToolTip("Run %s" % rule.name)
            button.clicked.connect(
                lambda _=False, r=rule: self.run_one(r))
            self.forms_layout.addWidget(button)

        trusted = core.document_trusted(self._document)
        has_code = len(rules) + len(code)
        untrusted = bool(has_code) and not trusted
        self.banner.setVisible(untrusted)
        self.allow_folder.setVisible(
            bool(getattr(self._document, "path", "")))
        if untrusted:
            parts = []
            if len(rules):
                parts.append("%d rule%s" % (len(rules),
                                            "" if len(rules) == 1 else "s"))
            if code:
                parts.append("%d code feature%s" % (
                    len(code), "" if len(code) == 1 else "s"))
            self.banner_text.setText(
                "This document carries %s that %s not been allowed to run. "
                "Only allow code from someone you trust."
                % (" and ".join(parts),
                   "has" if has_code == 1 else "have"))

    def trust(self) -> None:
        if self._document is None:
            return
        paths = core.trust_document(self._document)
        self._trusted(paths, "Code in this document may now run.")

    def trust_folder(self) -> None:
        path = getattr(self._document, "path", "")
        if not path:
            return
        folder = os.path.dirname(os.path.abspath(path))
        core.trust_folder(folder)
        paths = core.trust_document(self._document)
        self._trusted(paths, "Everything in %s may now run its code."
                      % folder)

    def _trusted(self, paths, message: str) -> None:
        self.host.status_message.setStyleSheet("")
        self.host.status_message.setText(message)
        after = getattr(self.host, "after_trust", None)
        if after is not None:
            after(self._document, paths)
        self.refresh()

    # -- commands ----------------------------------------------------------

    def selected(self) -> Tuple[Optional[str], Optional[int]]:
        item = self.tree.currentItem()
        if item is None:
            return None, None
        kind = item.data(0, ROLE_KIND)
        if kind not in ("rule", "code"):
            return kind, None
        return kind, int(item.data(0, ROLE_ID))

    def selected_rule(self) -> Optional[core.Rule]:
        kind, ident = self.selected()
        rules = self.rules
        if kind != "rule" or rules is None:
            return None
        return rules.get(ident)

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

    def new_code(self) -> None:
        make = getattr(self.host, "new_code_feature", None)
        if make is not None:
            make()

    def edit_rule(self, rule: core.Rule):
        dialog = RuleEditor(rule, self._document, self.host)
        dialog.model_changed.connect(self.changed)
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
        if not core.document_trusted(self._document):
            self.trust()
        results = rules.run_all(self._document)
        self._report(results)

    def run_one(self, rule: core.Rule) -> None:
        if not core.document_trusted(self._document):
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
        kind, ident = self.selected()
        if kind == "rule":
            rule = self.selected_rule()
            if rule is not None:
                self.edit_rule(rule)
        elif kind == "code":
            edit = getattr(self.host, "edit_feature", None)
            if edit is not None:
                edit(ident)

    def _menu(self, pos: QtCore.QPoint) -> None:
        rule = self.selected_rule()
        kind, ident = self.selected()
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
            pin = menu.addAction("Show as a button")
            pin.setCheckable(True)
            pin.setChecked(rule.button)
            pin.triggered.connect(lambda on: self._pin(rule, on))
            menu.addSeparator()
            menu.addAction("Delete").triggered.connect(
                lambda: self._delete(rule))
            menu.addSeparator()
        elif kind == "code":
            menu.addAction("Edit...").triggered.connect(
                lambda: self.host.edit_feature(ident))
            menu.addSeparator()
        menu.addAction("New rule").triggered.connect(self.new_rule)
        if self.code_button.isEnabled():
            menu.addAction("New code feature").triggered.connect(
                self.new_code)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _enable(self, rule: core.Rule, on: bool) -> None:
        rule.enabled = bool(on)
        self._document.modified = True
        self.refresh()

    def _pin(self, rule: core.Rule, on: bool) -> None:
        rule.button = bool(on)
        self._document.modified = True
        self.refresh()

    def _delete(self, rule: core.Rule) -> None:
        rules = self.rules
        if rules is None:
            return
        rules.remove(rule.id)
        self._document.modified = True
        self.refresh()


# ------------------------------------------------------------ code feature


class CodeDialog(dialogs.FeatureDialog):
    """Edit a code feature: the script, what it does to the part, and why
    it failed if it did.

    It does not rebuild on every keystroke, because half-typed code is
    broken code and a model flashing red on every letter is no help. It
    builds when asked, with Build, F5 or Ctrl+Enter, and on OK.
    """

    def __init__(self, host, feature, is_new, snapshot=None):
        super().__init__(host, feature, "Code Feature", "code", is_new,
                         snapshot)
        self.setMinimumSize(720, 500)
        self.resize(980, 700)

    def build(self):
        self.name_edit = QtWidgets.QLineEdit(self.feature.name)
        self.form.add("Name", self.name_edit)
        self.boolean_row()

        self.script = ScriptEditor(self.doc, "code",
                                   self.feature.source or CODE_STARTER)
        self.script.run_requested.connect(self.preview)
        self.script.editor.textChanged.connect(self._edited)

        root = self.layout()
        root.insertWidget(2, self.script, 1)
        # the base keeps a stretch under the status line; the editor has
        # that room now
        for i in range(root.count()):
            if root.itemAt(i).spacerItem() is not None:
                root.setStretch(i, 0)

        row = root.itemAt(root.count() - 1).layout()
        build = QtWidgets.QPushButton("Build")
        build.setIcon(icons.icon("update", 16))
        build.setToolTip("Run the script and show what it makes "
                         "(F5 or Ctrl+Enter)")
        build.clicked.connect(self.preview)
        row.insertWidget(1, build)
        if not core.document_trusted(self.doc):
            allow = QtWidgets.QPushButton("Allow code")
            allow.setToolTip("This document's code has not been allowed to "
                             "run.  Allow it for this session.")
            allow.clicked.connect(lambda: self._allow(allow))
            row.insertWidget(1, allow)
        QtGui.QShortcut(QtGui.QKeySequence("F5"), self, self.preview)

    def _allow(self, button) -> None:
        panel = getattr(self.host, "rules_panel", None)
        if panel is not None:
            panel.set_document(self.doc)
            panel.trust()
        else:
            core.trust_document(self.doc)
        button.hide()
        self.preview()

    def load(self):
        self.load_boolean()

    def store(self):
        self.feature.source = self.script.text()
        self.feature.name = self.name_edit.text().strip() or self.feature.name
        self.store_boolean()

    def preview(self):
        super().preview()
        if self._closed:
            return
        error = self.feature.error
        if error:
            self.script.show_result(error, False, _line_of(error))
        else:
            said = getattr(self.feature, "output", "") or ""
            self.script.show_result(said or "Built.", True)

    def _edited(self):
        if self._updating or self._closed:
            return
        self.status.setText("Changed. Build (F5) to see it.")
        self.status.setStyleSheet("color: %s;" % C.text_dim)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        # Escape throws away a feature; in a window full of typing that is
        # one stray key from losing an hour, so here it does nothing
        if event.key() == QtCore.Qt.Key_Escape:
            event.accept()
            return
        super().keyPressEvent(event)


dialogs.DIALOGS[CodeFeature] = CodeDialog


# ------------------------------------------------------------------- forms


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
        self.setWindowIcon(icons.icon("auto", 24))
        self.setMinimumWidth(380)
        # its sliders keep moving while the model rebuilds in a worker: the
        # value they end on is the one built next
        self.setProperty("datum_live", True)

        # what to put back if this is cancelled
        self._before = {name: document.params[name].expression
                        for name in document.params.names()}
        self._material = getattr(document, "material", None)

        self._pending = QtCore.QTimer(self)
        self._pending.setSingleShot(True)
        self._pending.setInterval(120)
        self._pending.timeout.connect(self._rebuild)

        # Spare cores build the values the controls are about to reach, so
        # arriving at one shows it rather than builds it. Topped up now and
        # then as cores come free, near whatever was touched last.
        self._ahead = None
        start = getattr(host, "build_ahead", None)
        if start is not None:
            try:
                self._ahead = start(document)
            except Exception:
                self._ahead = None
        if self._ahead is not None:
            document.ahead = self._ahead
        self._focus = None
        self._topped = None
        self._refill = QtCore.QTimer(self)
        self._refill.setInterval(200)
        self._refill.timeout.connect(self._top_up)
        if self._ahead is not None:
            self._refill.start()
        self.finished.connect(self._stop_ahead)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        form = QtWidgets.QFormLayout()
        form.setSpacing(8)
        self.widgets = {}
        # per control, the values it could be set to next, likeliest first
        self._neighbours = {}
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

        wants_param = not (control.kind == "choice"
                           and control.param == "material")
        if wants_param and control.param and \
                control.param not in self.document.params:
            # a form may name a parameter the model has not got yet, and
            # making it is friendlier than refusing to open
            start = control.low
            if control.kind == "choice" and control.options:
                try:
                    start = float(control.options[0])
                except ValueError:
                    start = 0.0
            self.document.params.add(control.param, repr(float(start)))
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

        def at(ticks: int) -> float:
            return control.low + ticks * step

        last = [slider.value()]

        def ahead_of(ticks: int):
            return [at(t) for t in core_ahead.around(
                ticks, ticks - last[0], slider.maximum())]

        def moved(ticks: int) -> None:
            value = at(ticks)
            readout.setText(("%g" % value))
            self._set(control.param, value)
            self._look_ahead(control.param, ahead_of(ticks))
            last[0] = ticks

        slider.valueChanged.connect(moved)
        moved(slider.value())
        self._neighbours[control.param] = lambda: ahead_of(slider.value())
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
        self._neighbours[control.param] = lambda: [
            box.value() + box.singleStep(), box.value() - box.singleStep()]
        return box

    def _choice(self, control):
        combo = QtWidgets.QComboBox()
        combo.addItems(control.options)
        # start on what the model already says, not on the first option
        if control.param == "material":
            current = getattr(self.document, "material", "")
            index = combo.findText(current)
        else:
            value = self._value(control.param)
            index = -1
            for i, option in enumerate(control.options):
                try:
                    if abs(float(option) - value) < 1e-9:
                        index = i
                        break
                except ValueError:
                    continue
        if index >= 0:
            combo.setCurrentIndex(index)
        combo.currentTextChanged.connect(
            lambda text, p=control.param: self._set_text(p, text))
        self.widgets[control.param] = combo
        if control.param != "material":
            def options():
                # the ones next to the current choice first
                here = combo.currentIndex()
                order = sorted(range(combo.count()),
                               key=lambda i: (abs(i - here), i))
                out = []
                for i in order:
                    try:
                        out.append(float(combo.itemText(i)))
                    except ValueError:
                        pass
                return out
            self._neighbours[control.param] = options
        return combo

    # -- driving the model -------------------------------------------------

    def _set(self, name: str, value: float) -> None:
        if not name:
            return
        text = repr(float(value))
        if self.document.params[name].expression == text:
            return
        self.document.params.set_expression(name, text)
        self.document.modified = True
        self._focus = name
        if self._ahead is not None and self._ahead.ready(self.document):
            # built ahead: shown now, not after the pause a drag waits for
            self._pending.stop()
            self._rebuild()
        else:
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
            catch_up = getattr(self.host, "_rules_changed", None)
            if catch_up is not None:
                catch_up()
            else:
                self.host.rebuild(keep_camera=True)
        except Exception:
            pass

    # -- building ahead ----------------------------------------------------

    def _look_ahead(self, name: str, values) -> None:
        if self._ahead is None or not name:
            return
        try:
            self._ahead.offer({name: v} for v in values)
        except Exception:
            pass

    def _top_up(self) -> None:
        """Give cores that came free something near where the form is now."""
        ahead = self._ahead
        if ahead is None or not ahead.free():
            return
        state = (tuple(self.document.params[n].expression
                       for n in self._neighbours
                       if n in self.document.params), ahead.freed)
        if state == self._topped:
            return      # nothing moved and nothing came free: asked already
        self._topped = state
        # the control touched last first, then the others in turn
        names = list(self._neighbours)
        if self._focus in names:
            names.remove(self._focus)
            names.insert(0, self._focus)
        lists = []
        for name in names:
            try:
                lists.append([(name, v) for v in self._neighbours[name]()])
            except Exception:
                continue
        variants = []
        depth = max((len(values) for values in lists), default=0)
        for i in range(depth):
            for values in lists:
                if i < len(values):
                    variants.append({values[i][0]: values[i][1]})
        try:
            ahead.offer(variants)
        except Exception:
            pass

    def _stop_ahead(self, *_args) -> None:
        self._refill.stop()
        ahead, self._ahead = self._ahead, None
        if ahead is None:
            return
        if getattr(self.document, "ahead", None) is ahead:
            self.document.ahead = None
        ahead.close()

    def reject(self) -> None:
        for name, expression in self._before.items():
            if name in self.document.params:
                self.document.params.set_expression(name, expression)
        if self._material is not None:
            self.document.material = self._material
        self._rebuild()
        super().reject()


def open_form(host, document, title: str, controls) -> bool:
    """What core.rules calls when a rule asks for a window."""
    dialog = FormDialog(host, document, title, controls)
    return dialog.exec() == QtWidgets.QDialog.Accepted
