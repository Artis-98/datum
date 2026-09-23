"""Projects: which folder the work goes into, and whose recent list it is."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtCore, QtWidgets                              # noqa: E402

from datum.core import fileformat                                  # noqa: E402
from datum.core.project import (                                   # noqa: E402
    DictStore, Project, ProjectError, ProjectManager, documents_folder,
)

FAILS = []
app = QtWidgets.QApplication(sys.argv)


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


HOME = tempfile.mkdtemp(prefix="datum_proj_home_")
WORK = tempfile.mkdtemp(prefix="datum_proj_work_")


# ==========================================================================
print("a fresh install already has somewhere to put things")

store = DictStore()
manager = ProjectManager(store, home=HOME)
default = manager.active()

check("there is an active project without anyone choosing one",
      default.valid, default.path)
check("it is called Default", default.name == "Default", default.name)
check("and it lives under Documents",
      os.path.normcase(default.folder)
      == os.path.normcase(documents_folder(HOME)), default.folder)
check("the file is really written", os.path.isfile(default.path))
check("asking again does not make a second one",
      manager.active().path == default.path)


# ==========================================================================
print("the project file travels with its folder")

check("the file sits in the folder it speaks for",
      os.path.dirname(default.path) == default.folder)
reopened = Project.load(default.path)
check("and reads back on its own", reopened.name == "Default", reopened.name)


# ==========================================================================
print("each project keeps its own recent list")

default.remember(os.path.join(default.folder, "Bracket.pdat"))
default.remember(os.path.join(default.folder, "Housing.pdat"),
                 fileformat.ASSEMBLY)
check("two files remembered", len(default.recent) == 2, default.recent)
check("newest first",
      default.recent[0]["path"].endswith("Housing.pdat"), default.recent[0])
check("the type is kept too",
      default.recent[0]["type"] == fileformat.ASSEMBLY, default.recent[0])

tote = manager.create("Tote", WORK)
check("a new project becomes the active one",
      manager.active().name == "Tote", manager.active().name)
check("it is where it was asked to be",
      os.path.normcase(tote.folder) == os.path.normcase(WORK), tote.folder)
check("and it starts with nothing in its list", tote.recent == [], tote.recent)

tote.remember(os.path.join(WORK, "Side Panel.pdat"))
check("its own list fills up", len(manager.active().recent) == 1)

manager.activate(default.path)
check("switching back brings the other list with it",
      len(manager.active().recent) == 2
      and manager.active().recent[0]["path"].endswith("Housing.pdat"),
      manager.active().recent)
check("and the Tote list is untouched",
      len(Project.load(tote.path).recent) == 1)

manager.active().clear_recent()
check("clearing empties only the active one",
      manager.active().recent == []
      and len(Project.load(tote.path).recent) == 1)


# ==========================================================================
print("the list of projects")

names = sorted(p.name for p in manager.known())
check("both are known about", names == ["Default", "Tote"], names)

manager.forget(tote.path)
check("forgetting drops it from the list",
      [p.name for p in manager.known()] == ["Default"],
      [p.name for p in manager.known()])
check("but leaves the file exactly where it was", os.path.isfile(tote.path))

manager.activate(tote.path)
check("and it can be added back", manager.active().name == "Tote")
manager.activate(default.path)

print("a project that has been deleted off disk stops being offered")
gone = manager.create("Temporary", WORK, activate=False)
os.remove(gone.path)
check("the dead one is dropped quietly",
      "Temporary" not in [p.name for p in manager.known()],
      [p.name for p in manager.known()])


# ==========================================================================
print("names that would not make a filename are refused")

for bad in ("", "   ", "with/slash", "a:colon", "star*"):
    try:
        manager.create(bad, WORK)
        check("refused %r" % bad, False, "it was accepted")
    except ProjectError:
        check("refused %r" % bad, True)

try:
    manager.create("Tote", WORK)
    check("a second project of the same name is refused", False)
except ProjectError:
    check("a second project of the same name is refused", True)


# ==========================================================================
print("the window, and the application it drives")

from datum.ui.main_window import MainWindow                        # noqa: E402
from datum.ui.projects import ProjectsDialog                       # noqa: E402

win = MainWindow()
win.resize(1200, 800)
win.show()
for _ in range(4):
    app.processEvents()

check("the window has a project to save into", bool(win.project_folder()),
      win.project_folder())
check("a suggested filename lands inside it",
      os.path.dirname(win.in_project("Part1.pdat")) == win.project_folder(),
      win.in_project("Part1.pdat"))
check("the start page names it",
      win.start_page.project_button.text()
      == win.start_page.projects.active().name,
      win.start_page.project_button.text())

dialog = ProjectsDialog(win.start_page.projects, win)
# the application connects this when it opens the dialog; the test opens it
# directly, so it has to make the same connection to see the same behaviour
dialog.activated.connect(lambda _p: win.start_page.refresh())
for _ in range(2):
    app.processEvents()
check("the dialog lists the projects", dialog.list.count() >= 1,
      dialog.list.count())
check("the active one is selected to begin with",
      os.path.normcase(dialog.selected() or "")
      == os.path.normcase(win.start_page.projects.active_path()),
      dialog.selected())
check("and cannot be removed, since there would be nowhere to save",
      not dialog.remove_button.isEnabled())
check("nor made active twice", not dialog.active_button.isEnabled())

# switch to another one through the dialog, exactly as a click would
other = win.start_page.projects.create("Second", WORK, activate=False)
dialog.refresh()
for row in range(dialog.list.count()):
    item = dialog.list.item(row)
    if os.path.normcase(item.data(QtCore.Qt.UserRole)) == os.path.normcase(other.path):
        dialog.list.setCurrentItem(item)
check("another project can be selected",
      os.path.normcase(dialog.selected() or "")
      == os.path.normcase(other.path), dialog.selected())
check("and then made active", dialog.active_button.isEnabled())
dialog.make_active()
for _ in range(2):
    app.processEvents()
check("the application followed it",
      os.path.normcase(win.project_folder()) == os.path.normcase(WORK),
      win.project_folder())
check("and so did the start page",
      win.start_page.project_button.text() == "Second",
      win.start_page.project_button.text())

print("saving a document files it under the project")
win.new_document(prompt=False)
for _ in range(2):
    app.processEvents()
suggested = win.in_project(win.document.title + ".pdat")
check("the save dialog would start in the project",
      os.path.normcase(os.path.dirname(suggested))
      == os.path.normcase(WORK), suggested)

written = win.document.save(suggested)
win.start_page.remember(written, fileformat.PART)
check("and it shows up in that project's recents",
      any(os.path.normcase(e["path"]) == os.path.normcase(written)
          for e in win.start_page.projects.active().recent),
      win.start_page.projects.active().recent)

win.start_page.projects.activate(default.path)
win.start_page.refresh()
for _ in range(2):
    app.processEvents()
check("switching away hides it again",
      not any(os.path.normcase(e["path"]) == os.path.normcase(written)
              for e in win.start_page.projects.active().recent),
      win.start_page.projects.active().recent)

for entry in list(win.session.documents):
    entry.document.modified = False
    win.close_entry(entry)
win.close()
for _ in range(3):
    app.processEvents()

print()
if FAILS:
    print("%d FAILED" % len(FAILS))
    for name in FAILS:
        print("   - %s" % name)
    sys.exit(1)
print("all project checks passed")
