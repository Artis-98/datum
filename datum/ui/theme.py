"""Colour palette and application stylesheet."""

from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6 import QtGui

from ..core import prefs as user_prefs


class C:
    """Named colours - one place to retune the whole application."""

    # Chrome.  Neutral greys with a trace of blue left in them, so the
    # silver of the mark reads as metal against the window rather than as
    # a lighter patch of the same colour.
    window = "#1e2124"
    ribbon = "#2a2e32"
    ribbon_tab = "#212528"
    ribbon_tab_active = "#2a2e32"
    panel = "#24282c"
    panel_alt = "#2e3338"
    border = "#3a4046"
    border_light = "#4b535b"

    # text
    text = "#dfe3e8"
    text_dim = "#98a1aa"
    text_bright = "#ffffff"

    # The accent is the mark's own silver.  It is a light colour where the
    # old blue was a dark one, so anything that fills with it needs dark
    # lettering on top - that is what ``on_accent`` is for, and every
    # caller uses it rather than reaching for white.
    accent = "#c2ccd8"
    accent_dark = "#9aa7b6"
    accent_soft = "#3d4854"           # fills behind ordinary text
    on_accent = "#171a20"             # lettering on an accent fill

    # semantics
    error = "#e05561"
    warn = "#e0a355"
    ok = "#69c26b"
    sketch_line = "#e8ecef"          # fully constrained
    sketch_free = "#9a8cf2"          # still has degrees of freedom
    sketch_construction = "#8a93a0"
    sketch_ground = "#67d08a"
    sketch_dim = "#78c8a0"
    sketch_selected = "#ffcf4d"
    # A sketch point runs through four states as you work it, and each one
    # has to be readable at a glance: green under the cursor, blue once it is
    # picked, yellow while it is being dragged, and green again the moment it
    # magnetises onto another point and is about to be mated to it.
    sketch_hover = "#4ee08a"
    sketch_picked = "#2f7fd6"
    sketch_drag = "#ffcf4d"
    sketch_magnet = "#4ee08a"
    sketch_fixed = "#67b96a"
    sketch_preview = "#aebbc9"

    # 3d
    bg_top = (0.168, 0.188, 0.208)
    bg_bottom = (0.082, 0.090, 0.098)
    material = (0.620, 0.667, 0.718)
    material_edge = (0.129, 0.145, 0.161)
    highlight = (1.000, 0.812, 0.302)
    preselect = (0.761, 0.800, 0.847)

    # what a picked thing turns on a sheet of paper, where the background
    # is white and the silver of the chrome would vanish
    paper_selected = "#5b6b7d"


FONT_STACK = '"Segoe UI", "Inter", system-ui, sans-serif'
MONO_STACK = '"Cascadia Mono", "Consolas", monospace'
# The wordmark wants a heavy grotesque with flat sides, not the interface
# font at a heavier weight - Segoe UI has no black cut, so it falls back to
# whichever of these is installed before settling for bolding the UI face.
WORDMARK_STACK = ('"Archivo Black", "Inter", "Segoe UI Variable Display",'
                  ' "Segoe UI Semibold", "Arial Black", sans-serif')


def qcolor(hex_or_tuple) -> QtGui.QColor:
    if isinstance(hex_or_tuple, str):
        return QtGui.QColor(hex_or_tuple)
    r, g, b = hex_or_tuple
    return QtGui.QColor(int(r * 255), int(g * 255), int(b * 255))


STYLESHEET = """
* {{
    font-family: {font};
    font-size: 12px;
}}

QWidget {{
    background: {window};
    color: {text};
}}

QMainWindow::separator {{
    background: {border};
    width: 1px;
    height: 1px;
}}

/* ---------------------------------------------------------------- ribbon */

#RibbonBar {{
    background: {ribbon};
    border-bottom: 1px solid {border};
}}

#RibbonQat {{
    background: {window};
    border-bottom: 1px solid {border};
}}

#RibbonTabStrip {{
    background: {ribbon_tab};
    border-bottom: 1px solid {border};
}}

#RibbonTabButton {{
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 6px 16px 5px 16px;
    color: {text_dim};
    font-size: 12px;
}}
#RibbonTabButton:hover {{
    color: {text};
    background: {panel_alt};
}}
#RibbonTabButton:checked {{
    color: {text_bright};
    background: {ribbon};
    border-bottom: 2px solid {accent};
    font-weight: 600;
}}

#RibbonPanel {{
    background: transparent;
    border-right: 1px solid {border};
}}

#QuickButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 3px;
}}
#QuickButton:hover {{
    background: {panel_alt};
    border: 1px solid {border_light};
}}
#QuickButton:pressed {{
    background: {accent_soft};
}}

#RibbonPanelTitle {{
    color: {text_dim};
    font-size: 10px;
    padding-top: 1px;
}}

#RibbonButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 3px;
    padding: 4px 6px;
    color: {text};
    text-align: center;
}}
#RibbonButton:hover {{
    background: {panel_alt};
    border: 1px solid {border_light};
}}
#RibbonButton:pressed, #RibbonButton:checked {{
    background: {accent_soft};
    border: 1px solid {accent};
}}
#RibbonButton:disabled {{
    color: #5b636c;
}}

#RibbonSmallButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 3px;
    padding: 2px 8px 2px 4px;
    color: {text};
    text-align: left;
}}
#RibbonSmallButton:hover {{
    background: {panel_alt};
    border: 1px solid {border_light};
}}
#RibbonSmallButton:pressed, #RibbonSmallButton:checked {{
    background: {accent_soft};
    border: 1px solid {accent};
}}
#RibbonSmallButton:disabled {{
    color: #5b636c;
}}

/* ----------------------------------------------------------- dock panels */

QDockWidget {{
    color: {text_dim};
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
}}
QDockWidget::title {{
    background: {panel_alt};
    padding: 5px 8px;
    border-bottom: 1px solid {border};
    font-size: 11px;
    font-weight: 600;
    color: {text};
}}

QTreeWidget, QTreeView, QListWidget, QTableWidget {{
    background: {panel};
    alternate-background-color: {panel_alt};
    border: none;
    outline: none;
    selection-background-color: {accent_soft};
}}
QTreeWidget::item, QListWidget::item {{
    padding: 3px 2px;
    border: none;
}}
QTreeWidget::item:hover, QListWidget::item:hover {{
    background: {panel_alt};
}}
QTreeWidget::item:selected, QListWidget::item:selected {{
    background: {accent_soft};
    color: {text_bright};
}}
QHeaderView::section {{
    background: {panel_alt};
    color: {text_dim};
    padding: 4px 6px;
    border: none;
    border-right: 1px solid {border};
    border-bottom: 1px solid {border};
}}
QTableWidget {{
    gridline-color: {border};
}}
QTableWidget::item:selected {{
    background: {accent_soft};
}}

/* --------------------------------------------------------------- inputs */

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {ribbon_tab};
    border: 1px solid {border};
    border-radius: 3px;
    padding: 4px 6px;
    color: {text};
    selection-background-color: {accent};
    /* the accent is light now, so highlighted text has to go dark */
    selection-color: {on_accent};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus {{
    border: 1px solid {accent};
}}
QLineEdit[invalid="true"] {{
    border: 1px solid {error};
}}
QLineEdit:disabled, QComboBox:disabled {{
    color: #626a73;
    background: #24272b;
}}

QComboBox::drop-down {{
    border: none;
    width: 18px;
}}
QComboBox QAbstractItemView {{
    background: {panel_alt};
    border: 1px solid {border_light};
    selection-background-color: {accent_soft};
    outline: none;
}}

QPushButton {{
    background: {panel_alt};
    border: 1px solid {border_light};
    border-radius: 3px;
    padding: 5px 14px;
    color: {text};
    min-width: 68px;
}}
QPushButton:hover {{
    background: {border};
    border-color: {accent};
}}
QPushButton:pressed {{
    background: {accent_soft};
}}
QPushButton:disabled {{
    color: #5b636c;
    background: #262a2e;
}}
QPushButton[primary="true"] {{
    background: {accent_dark};
    border-color: {accent};
    color: {on_accent};
    font-weight: 600;
}}
QPushButton[primary="true"]:hover {{
    background: {accent};
}}

QCheckBox, QRadioButton {{
    spacing: 6px;
    padding: 2px 0;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 13px;
    height: 13px;
    border: 1px solid {border_light};
    background: {ribbon_tab};
    border-radius: 2px;
}}
QRadioButton::indicator {{
    border-radius: 7px;
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {accent};
    border-color: {accent};
}}

QGroupBox {{
    border: 1px solid {border};
    border-radius: 3px;
    margin-top: 9px;
    padding-top: 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
    color: {text_dim};
    font-size: 11px;
}}

QLabel[hint="true"] {{
    color: {text_dim};
    font-size: 11px;
}}
QLabel[error="true"] {{
    color: {error};
}}

/* -------------------------------------------------------------- various */

QToolTip {{
    background: {panel_alt};
    color: {text};
    border: 1px solid {border_light};
    padding: 4px 6px;
}}

QStatusBar {{
    background: {ribbon_tab};
    border-top: 1px solid {border};
    color: {text_dim};
}}
QStatusBar::item {{ border: none; }}

QScrollBar:vertical {{
    background: transparent;
    width: 11px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {border_light};
    border-radius: 5px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: {accent}; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 11px;
}}
QScrollBar::handle:horizontal {{
    background: {border_light};
    border-radius: 5px;
    min-width: 24px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QMenu {{
    background: {panel_alt};
    border: 1px solid {border_light};
    padding: 4px;
}}
QMenu::item {{
    padding: 5px 24px 5px 10px;
    border-radius: 2px;
}}
QMenu::item:selected {{ background: {accent_soft}; }}
QMenu::separator {{
    height: 1px;
    background: {border};
    margin: 4px 6px;
}}

QSplitter::handle {{ background: {border}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}

QProgressBar {{
    border: 1px solid {border};
    border-radius: 2px;
    background: {ribbon_tab};
    text-align: center;
    height: 6px;
}}
QProgressBar::chunk {{ background: {accent}; }}
"""


def stylesheet() -> str:
    return BROWSER_MODE_CSS + STYLESHEET.format(
        font=FONT_STACK,
        window=C.window,
        ribbon=C.ribbon,
        ribbon_tab=C.ribbon_tab,
        panel=C.panel,
        panel_alt=C.panel_alt,
        border=C.border,
        border_light=C.border_light,
        text=C.text,
        text_dim=C.text_dim,
        text_bright=C.text_bright,
        accent=C.accent,
        accent_dark=C.accent_dark,
        accent_soft=C.accent_soft,
        on_accent=C.on_accent,
        error=C.error,
    )


BROWSER_MODE_CSS = """
#BrowserModeBar { background: %(panel)s;
                  border-bottom: 1px solid %(border)s; }
#BrowserModeTab { color: %(dim)s; background: transparent; border: none;
                  border-bottom: 2px solid transparent;
                  padding: 3px 10px 4px 10px; font-size: 11px; }
#BrowserModeTab:hover { color: %(text)s; }
#BrowserModeTab:checked { color: %(text)s;
                          border-bottom: 2px solid %(accent)s; }
""" % {"panel": C.panel, "border": C.border, "dim": C.text_dim,
       "text": C.text, "accent": C.accent}


# --------------------------------------------------------------- overrides
#
# The palette above is what DATUM ships with.  A user may repaint a few of
# it from Preferences - the viewport background and the sketch line
# colours - and those are theirs, not the document's, so they live in the
# preferences file and are laid over the palette at startup.

_SHIPPED: Dict[str, Any] = {name: value for name, value in vars(C).items()
                            if not name.startswith("_")
                            and isinstance(value, (str, tuple))}


def shipped(key: str) -> Any:
    """The colour DATUM came with, whatever it has been set to since."""
    return _SHIPPED.get(key)


def as_hex(value: Any) -> str:
    """A palette entry as "#rrggbb", whether it is stored that way or not.

    The viewport wants its background as floats and everything else wants
    a string, which is an implementation detail no colour picker should
    have to know about.
    """
    if isinstance(value, tuple):
        return "#%02x%02x%02x" % tuple(
            max(0, min(255, int(round(c * 255.0)))) for c in value[:3])
    return str(value)


def _as_stored(key: str, text: str) -> Any:
    if isinstance(_SHIPPED.get(key), tuple):
        text = text.lstrip("#")
        return tuple(int(text[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return text


def apply_colours(mapping: Optional[Dict[str, str]] = None) -> None:
    """Lay the user's colours over the palette, or take them off again."""
    chosen = user_prefs.prefs().colours if mapping is None else mapping
    for key, shipped_value in _SHIPPED.items():
        text = chosen.get(key)
        try:
            setattr(C, key, _as_stored(key, text) if text else shipped_value)
        except (ValueError, IndexError):
            setattr(C, key, shipped_value)
