"""Application entry point."""

from __future__ import annotations

import ctypes
import os
import sys
import traceback

from PySide6 import QtCore, QtGui, QtWidgets

from . import APP_NAME, __version__


def _excepthook(kind, value, tb) -> None:
    """Report an unexpected error instead of vanishing."""
    text = "".join(traceback.format_exception(kind, value, tb))
    sys.stderr.write(text)
    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    box = QtWidgets.QMessageBox()
    box.setIcon(QtWidgets.QMessageBox.Critical)
    box.setWindowTitle("%s - unexpected error" % APP_NAME)
    box.setText("Something went wrong, but the model is still open.\n\n%s: %s"
                % (kind.__name__, value))
    box.setDetailedText(text)
    box.exec()


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)

    QtCore.QCoreApplication.setAttribute(
        QtCore.Qt.AA_UseHighDpiPixmaps, True)

    app = QtWidgets.QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("IITEG")

    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "IITEG.DATUM.%s" % __version__)
        except Exception:
            pass

    from .ui import icons
    from .ui.main_window import MainWindow
    from .ui.theme import stylesheet

    app.setWindowIcon(icons.app_icon())
    app.setStyleSheet(stylesheet())
    app.setStyle("Fusion")
    app.setStyleSheet(stylesheet())

    sys.excepthook = _excepthook

    window = MainWindow()
    # start maximised, like every other CAD package - and it avoids the
    # viewport being sized before the window manager has settled
    window.showMaximized()

    from .core import fileformat

    openable = tuple(fileformat.EXTENSIONS.values()) + (
        fileformat.LEGACY_EXTENSION,)
    for arg in argv[1:]:
        if arg.lower().endswith(openable) and os.path.exists(arg):
            window.open_path(arg)
            break

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
