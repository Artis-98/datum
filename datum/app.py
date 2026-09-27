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


def selftest(report_path: str = "") -> int:
    """Prove a built copy actually works, without opening a window.

    This exists because of one specific failure.  OpenCASCADE arrives as a
    single compiled module that links VTK, so a build that quietly leaves
    the VTK DLLs out still produces a perfectly good looking DATUM.exe
    that dies on the first import with "DLL load failed".  A build that is
    never started before it is published is a build that ships that.

    The result goes to a file rather than to the console, because a
    windowed build has no console to write to: printing from one goes
    nowhere, which is no use to whoever is trying to find out why the
    build is broken.  Each step is written as it finishes, so a run that
    hangs still says how far it got.
    """
    import tempfile

    path = report_path or os.path.join(tempfile.gettempdir(),
                                       "datum-selftest.txt")
    steps = []

    def note(text):
        steps.append(text)
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(os.linesep.join(steps) + os.linesep)
        except OSError:
            pass
        if sys.stdout is not None:
            try:
                print(text, flush=True)
            except (ValueError, OSError):
                pass

    try:
        note("selftest %s starting" % __version__)
        from .core import hlr, kernel
        from .core.document import Document
        from .core.features import PrimitiveFeature
        note("ok  imported the kernel (OpenCASCADE loaded)")

        from .core import workers
        if workers.ENABLED:
            helpers = workers.Pool(size=1)
            try:
                helpers.start()
                if not helpers.wait_ready(timeout=90):
                    raise RuntimeError("a worker process did not start")
                answer = helpers.submit("ping").result(timeout=30)
                note("ok  a worker process answered (pid %s)"
                     % answer.get("pid"))
            finally:
                helpers.shutdown()

        document = Document()
        for kind, a, b, c, operation in (("box", 40, 30, 10, "new"),
                                         ("cylinder", 5, 40, 0, "cut")):
            feature = PrimitiveFeature()
            (feature.kind, feature.a, feature.b, feature.c,
             feature.operation) = kind, str(a), str(b), str(c), operation
            document.add_feature(feature)
        document.rebuild()
        if document.shape is None or document.shape.IsNull():
            note("FAIL  the kernel built nothing")
            return 1
        note("ok  built a solid, volume %.1f mm3"
             % kernel.volume(document.shape))

        projection = hlr.project(document.shape, *hlr.ORIENTATIONS["front"])
        if not projection.ok or not projection.lines:
            note("FAIL  nothing projected: %s" % projection.error)
            return 1
        note("ok  projected %d line(s)" % len(projection.lines))

        from PySide6 import QtWidgets as _widgets
        application = (_widgets.QApplication.instance()
                       or _widgets.QApplication([]))
        from .ui import icons
        from .ui.main_window import MainWindow          # noqa: F401
        icons.app_icon()
        note("ok  Qt started and the interface imported")

        # Export really is exercised, not just imported.  The build's
        # exclude list is what keeps PySide6 from adding hundreds of
        # megabytes, and the way it goes wrong is by dropping a module
        # that only one feature needs: QtSvg for SVG, QtPrintSupport for
        # PDF and printing.  Importing them proves they are bundled;
        # writing a file with them proves they work.
        import tempfile as _temp

        from .core import drawing as _dwg
        from .ui import drawingexport

        sheet_doc = _dwg.DrawingDocument()
        sheet = sheet_doc.add_sheet("A4")
        folder = _temp.mkdtemp(prefix="datum-selftest-")
        pdf = drawingexport.to_pdf(sheet_doc, os.path.join(folder, "t.pdf"))
        svg = drawingexport.to_svg(sheet_doc, sheet,
                                   os.path.join(folder, "t.svg"))
        if os.path.getsize(pdf) < 500 or os.path.getsize(svg) < 200:
            note("FAIL  export produced an empty file")
            return 1
        note("ok  wrote a PDF (%d bytes) and an SVG (%d bytes)"
             % (os.path.getsize(pdf), os.path.getsize(svg)))
        import shutil as _shutil
        _shutil.rmtree(folder, ignore_errors=True)

        application.quit()

        note("PASS  DATUM %s is a working build" % __version__)
        return 0
    except Exception as exc:                            # noqa: BLE001
        import traceback
        note("FAIL  %s: %s" % (type(exc).__name__, exc))
        note(traceback.format_exc())
        return 1


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)

    if "--worker" in argv:
        # a copy of DATUM doing geometry for the window: no Qt, no window,
        # just requests on stdin (see core/worker.py)
        from .core import worker
        return worker.serve()
    if "--selftest" in argv:
        where = argv.index("--selftest")
        report = argv[where + 1] if len(argv) > where + 1 else ""
        return selftest(report)
    if "--version" in argv:
        print(__version__)
        return 0

    QtCore.QCoreApplication.setAttribute(
        QtCore.Qt.AA_UseHighDpiPixmaps, True)

    app = QtWidgets.QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("IITEG")

    if sys.platform == "win32":
        try:
            # No version in the id.  Windows keys taskbar pins and jump
            # lists off this string, so putting the version in it would
            # quietly break somebody's pinned shortcut on every update.
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "IITEG.DATUM")
        except Exception:
            pass

    from .ui import icons
    from .ui.main_window import MainWindow
    from .ui.theme import apply_colours, stylesheet

    # the user's own colours go on before anything is built with them
    apply_colours()

    app.setWindowIcon(icons.app_icon())
    app.setStyleSheet(stylesheet())
    app.setStyle("Fusion")
    app.setStyleSheet(stylesheet())

    sys.excepthook = _excepthook

    window = MainWindow()
    # start maximised, like every other CAD package - and it avoids the
    # viewport being sized before the window manager has settled
    window.showMaximized()

    # After the window is up, never before: a staged update or a check is
    # not worth delaying the thing the person actually asked for.
    QtCore.QTimer.singleShot(1200, window.updater.on_start)

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
