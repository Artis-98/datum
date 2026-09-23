"""Screenshot the project system: the rail, and the Projects window."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-shots"

from PySide6 import QtCore, QtGui, QtWidgets                       # noqa: E402

from datum.ui import icons                                         # noqa: E402
from datum.ui.main_window import MainWindow                        # noqa: E402
from datum.ui.projects import ProjectsDialog                       # noqa: E402
from datum.ui.theme import stylesheet                              # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "."
os.makedirs(OUT, exist_ok=True)

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
app.setWindowIcon(icons.app_icon())
win = MainWindow()
win.resize(1500, 950)
win.show()

_n = [0]


def settle(ms=500):
    timer = QtCore.QElapsedTimer()
    timer.start()
    while timer.elapsed() < ms:
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 20)


def shot(name, extra=None):
    settle(200)
    _n[0] += 1
    chrome = win.grab()
    if extra is not None and extra.isVisible():
        p = QtGui.QPainter(chrome)
        p.drawPixmap(win.mapFromGlobal(extra.mapToGlobal(QtCore.QPoint(0, 0))),
                     extra.grab())
        p.end()
    chrome.save(os.path.join(OUT, "%02d_%s.png" % (_n[0], name)))
    print("shot:", name)


def go():
    projects = win.start_page.projects
    work = tempfile.mkdtemp(prefix="datum_shot_projects_")
    for name in ("Plywood Tote", "Helmet Hub"):
        folder = os.path.join(work, name.replace(" ", "-"))
        os.makedirs(folder, exist_ok=True)
        try:
            made = projects.create(name, folder, activate=False)
            made.remember(os.path.join(folder, "Side Panel.pdat"))
            made.remember(os.path.join(folder, "End Panel.pdat"))
        except Exception as exc:
            print("skipped %s: %s" % (name, exc))

    win.show_start_page()
    win.start_page.refresh()
    settle(400)
    shot("home_with_project")

    dialog = ProjectsDialog(projects, win)
    dialog.activated.connect(lambda _p: win.start_page.refresh())
    dialog.move(win.mapToGlobal(QtCore.QPoint(360, 200)))
    dialog.show()
    settle(400)
    shot("projects_window", dialog)

    # switch to one of them and show the home page following it
    for row in range(dialog.list.count()):
        item = dialog.list.item(row)
        if "Plywood Tote" in item.text():
            dialog.list.setCurrentItem(item)
            dialog.make_active()
    settle(400)
    shot("after_switch", dialog)
    dialog.close()

    win.start_page.refresh()
    settle(300)
    shot("home_after_switch")
    print("DONE")
    app.quit()


QtCore.QTimer.singleShot(1500, go)
sys.exit(app.exec())
