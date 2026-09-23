"""The update banner: what it says, and what each button does."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtCore, QtWidgets                          # noqa: E402

from datum import __version__                                  # noqa: E402
from datum.core import update                                  # noqa: E402
from datum.ui.main_window import MainWindow                    # noqa: E402
from datum.ui.updater import UpdateBanner                      # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


# ==========================================================================
print("the banner keeps out of the way until it has something to say")

banner = UpdateBanner()
check("it starts hidden", not banner.isVisible())

banner.offer("0.3.0", "Hatched sections.")
check("an offer names both versions",
      "0.3.0" in banner.message.text() and __version__
      in banner.message.text(), banner.message.text())
check("the button says Update", banner.action.text() == "Update",
      banner.action.text())
check("Skip is offered", banner.skip.isVisible() or banner.skip.isEnabled())
check("What's New is offered, because there are notes",
      banner.notes_button.isVisible() or not banner.isVisible())
check("no progress bar yet", not banner.bar.isVisible()
      or not banner.isVisible())

print("an offer with no notes does not offer to show them")
quiet = UpdateBanner()
quiet.offer("0.3.0", "")
check("no What's New button", not quiet.notes_button.isVisible())


# ==========================================================================
print("while it works it shows progress instead of buttons")

banner.working("Downloading DATUM 0.3.0", 2, 7)
check("it says what it is doing", "Downloading" in banner.message.text())
check("the bar is the one reporting", banner.bar.value() == 2
      and banner.bar.maximum() == 7,
      (banner.bar.value(), banner.bar.maximum()))
check("and there is nothing to press", not banner.action.isVisible()
      or not banner.isVisible())


# ==========================================================================
print("when it is staged the button becomes the restart")

pressed = []
banner.apply_requested.connect(lambda: pressed.append("apply"))
banner.download_requested.connect(lambda: pressed.append("download"))

banner.ready("0.3.0")
check("it says what to do next", "Restart" in banner.message.text(),
      banner.message.text())
check("the button changed", banner.action.text() == "Restart Now",
      banner.action.text())
banner.action.click()
check("and pressing it asks to apply, not to download again",
      pressed == ["apply"], pressed)

print("whereas in the offer state the same button downloads")
pressed.clear()
banner.offer("0.3.0")
banner.action.click()
check("it asks to download", pressed == ["download"], pressed)


# ==========================================================================
print("a problem is reported in the banner, not in a dialog")

banner.problem("cannot reach api.iiteg.com")
check("it says so", "api.iiteg.com" in banner.message.text())
check("with nothing to press", not banner.action.isVisible()
      or not banner.isVisible())

print("and it can be dismissed")
closed = []
banner.dismissed.connect(lambda: closed.append(True))
banner._closed()
check("it goes away", not banner.isVisible())
check("and says it was dismissed", closed == [True])


# ==========================================================================
print("the window carries one, and it costs no space when idle")

win = MainWindow()
win.resize(1400, 900)
win.show()
QtWidgets.QApplication.processEvents()

check("the window has an updater", hasattr(win, "updater"))
check("its banner is in the layout",
      win.updater.banner.parent() is not None)
check("and hidden, so it takes no room",
      not win.updater.banner.isVisible())
check("the menu offers a manual check",
      any("Check for Updates" in a.text() for a in win.file_menu.actions()),
      [a.text() for a in win.file_menu.actions()])


# ==========================================================================
print("a source checkout never offers to update itself")

win.updater.on_start()
QtWidgets.QApplication.processEvents()
check("nothing appeared", not win.updater.banner.isVisible())

print("and asking directly gets an honest answer rather than silence")
allowed, why = update.can_update()
check("it knows it cannot", not allowed)
check("and the reason mentions source", "source" in why, why)


# ==========================================================================
print("a staged update is announced on the next start")

staged = tempfile.mkdtemp(prefix="datum_ui_update_")
plan = update.Plan(version="9.9.9", target=staged)
update.write_plan(plan)
check("it is pending", update.pending(staged) == "9.9.9")
win.updater.banner.ready(update.pending(staged))
check("the banner says so", "9.9.9" in win.updater.banner.message.text(),
      win.updater.banner.message.text())
update.clear_staging(staged)
check("and discarding it clears the flag", update.pending(staged) is None)


# ==========================================================================
print()
win.close()
if FAILS:
    print("%d FAILED" % len(FAILS))
    for name in FAILS:
        print("   - %s" % name)
    sys.exit(1)
print("all updater UI checks passed")
