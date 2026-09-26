"""Telling somebody there is a new DATUM, and putting it there if they want it.

A banner rather than a dialog.  An update is never urgent enough to take
the window away from somebody in the middle of a sketch, and a modal box
that appears while you are working is the reason people turn update checks
off.  The banner sits under the ribbon, says what is available, and waits.

Everything slow happens on a worker thread.  The check is a network round
trip and the download can be hundreds of megabytes on a dependency bump;
neither belongs on the thread that is drawing the application.
"""

from __future__ import annotations

from typing import Optional

from PySide6 import QtCore, QtWidgets

from .. import __version__
from ..core import swap, update
from . import icons
from .theme import C

SETTINGS_FEED = "update/feed"
SETTINGS_ENABLED = "update/check_on_start"
SETTINGS_SKIPPED = "update/skipped_version"
SETTINGS_LAST = "update/last_check"

# Long enough that it is not pestering a server on every launch, short
# enough that a fix put out on Monday is on the bench by Tuesday.
CHECK_INTERVAL = 6 * 60 * 60


class Worker(QtCore.QThread):
    """One job, off the main thread, reporting back by signal."""

    found = QtCore.Signal(object)          # Release
    planned = QtCore.Signal(object, object)   # Release, Plan
    staged = QtCore.Signal(object)         # Plan
    progress = QtCore.Signal(int, int)
    failed = QtCore.Signal(str)

    def __init__(self, parent, job: str, feed: str = "",
                 release=None, plan=None) -> None:
        super().__init__(parent)
        self.job = job
        self.feed = feed or update.DEFAULT_FEED
        self.release = release
        self.plan = plan
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            if self.job == "check":
                self._check()
            elif self.job == "download":
                self._download()
        except update.UpdateError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:                      # noqa: BLE001
            # A worker thread that dies silently leaves a banner spinning
            # for ever, so anything unexpected is reported too.
            self.failed.emit("the update check failed: %s" % exc)

    def _check(self) -> None:
        release = update.check(self.feed)
        if not release.newer:
            self.found.emit(None)
            return
        self.found.emit(release)

    def _download(self) -> None:
        import json

        raw = update.fetch_signed(self.release.base, self.release.manifest)
        manifest = json.loads(raw.decode("utf-8"))
        plan = update.plan_for(manifest, update.install_dir(),
                               self.release.version)
        self.planned.emit(self.release, plan)
        if plan.empty:
            self.staged.emit(plan)
            return
        update.download(plan, self.release, progress=self._tick)
        self.staged.emit(plan)

    def _tick(self, done: int, total: int) -> bool:
        self.progress.emit(done, total)
        return not self._cancelled


class UpdateBanner(QtWidgets.QFrame):
    """The strip that appears when there is something to say."""

    apply_requested = QtCore.Signal()
    download_requested = QtCore.Signal()
    dismissed = QtCore.Signal()
    skip_requested = QtCore.Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("UpdateBanner")
        self.setVisible(False)
        self.setStyleSheet(
            "#UpdateBanner { background: %s; border-bottom: 1px solid %s; }"
            % (C.panel_alt, C.accent))

        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(12, 7, 10, 7)
        row.setSpacing(10)

        self.mark = QtWidgets.QLabel()
        self.mark.setPixmap(icons.mark_pixmap(18))
        self.mark.setStyleSheet("background: transparent;")
        row.addWidget(self.mark)

        self.message = QtWidgets.QLabel("")
        self.message.setStyleSheet("background: transparent; color: %s;"
                                   % C.text)
        row.addWidget(self.message, 1)

        self.bar = QtWidgets.QProgressBar()
        self.bar.setFixedWidth(150)
        self.bar.setTextVisible(False)
        self.bar.setVisible(False)
        row.addWidget(self.bar)

        self.notes_button = QtWidgets.QPushButton("What's New")
        self.notes_button.setVisible(False)
        row.addWidget(self.notes_button)

        self.action = QtWidgets.QPushButton("Update")
        self.action.setProperty("primary", True)
        self.action.clicked.connect(self._acted)
        row.addWidget(self.action)

        self.skip = QtWidgets.QPushButton("Skip")
        self.skip.setToolTip("Do not offer this version again")
        self.skip.clicked.connect(self.skip_requested.emit)
        row.addWidget(self.skip)

        close = QtWidgets.QToolButton()
        close.setText("✕")
        close.setCursor(QtCore.Qt.PointingHandCursor)
        close.setStyleSheet(
            "QToolButton { background: transparent; border: none;"
            " color: %s; font-size: 13px; padding: 2px 4px; }"
            "QToolButton:hover { color: %s; }" % (C.text_dim, C.text))
        close.clicked.connect(self._closed)
        row.addWidget(close)

        self._mode = "offer"

    # ----------------------------------------------------------- appearance

    def offer(self, version: str, notes: str = "") -> None:
        self._mode = "offer"
        self.message.setText(
            "DATUM %s is available.  You are on %s." % (version, __version__))
        self.action.setText("Update")
        self.action.setEnabled(True)
        self.action.setVisible(True)
        self.skip.setVisible(True)
        self.bar.setVisible(False)
        self.notes_button.setVisible(bool(notes))
        self.setVisible(True)

    def working(self, text: str, done: int = 0, total: int = 0) -> None:
        self._mode = "working"
        self.message.setText(text)
        self.action.setVisible(False)
        self.skip.setVisible(False)
        self.notes_button.setVisible(False)
        self.bar.setVisible(True)
        self.bar.setRange(0, total or 0)
        self.bar.setValue(done)
        self.setVisible(True)

    def ready(self, version: str) -> None:
        self._mode = "ready"
        self.message.setText(
            "DATUM %s is ready.  Restart to finish." % version)
        self.action.setText("Restart Now")
        self.action.setEnabled(True)
        self.action.setVisible(True)
        self.skip.setVisible(False)
        self.bar.setVisible(False)
        self.notes_button.setVisible(False)
        self.setVisible(True)

    def problem(self, text: str) -> None:
        self._mode = "problem"
        self.message.setText(text)
        self.action.setVisible(False)
        self.skip.setVisible(False)
        self.bar.setVisible(False)
        self.notes_button.setVisible(False)
        self.setVisible(True)

    # -------------------------------------------------------------- signals

    def _acted(self) -> None:
        if self._mode == "ready":
            self.apply_requested.emit()
        else:
            self.download_requested.emit()

    def _closed(self) -> None:
        self.setVisible(False)
        self.dismissed.emit()


class UpdateController(QtCore.QObject):
    """Drives the check, the download and the restart."""

    def __init__(self, host) -> None:
        super().__init__(host)
        self.host = host
        self.banner = UpdateBanner(host)
        self.settings = QtCore.QSettings()
        self.release = None
        self.plan = None
        self.worker: Optional[Worker] = None

        self.banner.download_requested.connect(self.start_download)
        self.banner.apply_requested.connect(self.restart_now)
        self.banner.skip_requested.connect(self.skip_this)
        self.banner.notes_button.clicked.connect(self.show_notes)

    # ------------------------------------------------------------- settings

    @property
    def feed(self) -> str:
        return str(self.settings.value(SETTINGS_FEED, update.DEFAULT_FEED))

    @property
    def enabled(self) -> bool:
        value = self.settings.value(SETTINGS_ENABLED, True)
        return value in (True, "true", "True", 1, "1")

    def set_enabled(self, on: bool) -> None:
        self.settings.setValue(SETTINGS_ENABLED, bool(on))

    # ---------------------------------------------------------------- start

    def on_start(self) -> None:
        """Called once the window is up.

        A staged update outranks a check: if one is sitting there waiting,
        saying so is more useful than going and asking about a newer one.
        This is also what recovers a swap that did not finish, because the
        staging folder is still there and everything in it was verified
        before it was written.
        """
        waiting = update.pending()
        if waiting:
            self.banner.ready(waiting)
            return
        if not self.enabled:
            return
        allowed, _why = update.can_update()
        if not allowed:
            return
        if not self._due():
            return
        self.check(quiet=True)

    def _due(self) -> bool:
        import time
        try:
            last = float(self.settings.value(SETTINGS_LAST, 0) or 0)
        except (TypeError, ValueError):
            last = 0.0
        return (time.time() - last) > CHECK_INTERVAL

    # ---------------------------------------------------------------- check

    def check(self, quiet: bool = False) -> None:
        """Ask the feed what is out there.

        ``quiet`` is the automatic check on start-up: it says nothing when
        there is nothing to say.  Asked for from the menu it reports either
        way, because silence in answer to a direct question reads as a
        failure.
        """
        allowed, why = update.can_update()
        if not allowed:
            if not quiet:
                QtWidgets.QMessageBox.information(
                    self.host, "Check for Updates",
                    "Updates are not available here: %s." % why)
            return
        if self.worker is not None and self.worker.isRunning():
            return

        import time
        self.settings.setValue(SETTINGS_LAST, time.time())
        if not quiet:
            self.banner.working("Checking for updates...")

        self.worker = Worker(self, "check", self.feed)
        self.worker.found.connect(lambda r: self._found(r, quiet))
        self.worker.failed.connect(lambda m: self._failed(m, quiet))
        self.worker.start()

    def _found(self, release, quiet: bool) -> None:
        if release is None:
            if quiet:
                self.banner.setVisible(False)
            else:
                self.banner.problem("DATUM %s is the newest version."
                                    % __version__)
                QtCore.QTimer.singleShot(
                    6000, lambda: self.banner.setVisible(False))
            return
        if quiet and str(self.settings.value(SETTINGS_SKIPPED, "")) \
                == release.version:
            self.banner.setVisible(False)
            return
        self.release = release
        if release.needs_reinstall:
            self.banner.problem(
                "DATUM %s is available, but it is too big a jump to patch.  "
                "Download the installer from iiteg.com." % release.version)
            return
        self.banner.offer(release.version, release.notes)

    def _failed(self, message: str, quiet: bool) -> None:
        if quiet:
            # Nobody asked, so nobody needs a popup about the network.
            self.banner.setVisible(False)
            self.host.status_message.setText("Update check failed: %s"
                                             % message)
            return
        self.banner.problem(message)

    # ------------------------------------------------------------- download

    def start_download(self) -> None:
        if self.release is None:
            return
        if self.worker is not None and self.worker.isRunning():
            return
        self.banner.working("Working out what has changed...")
        self.worker = Worker(self, "download", self.feed, self.release)
        self.worker.planned.connect(self._planned)
        self.worker.progress.connect(self._progress)
        self.worker.staged.connect(self._staged)
        self.worker.failed.connect(lambda m: self.banner.problem(m))
        self.worker.start()

    def _planned(self, release, plan) -> None:
        self.plan = plan
        self.banner.working("Downloading DATUM %s: %s"
                            % (release.version, plan.summary()),
                            0, max(1, len(plan.download)))

    def _progress(self, done: int, total: int) -> None:
        self.banner.bar.setRange(0, total)
        self.banner.bar.setValue(done)

    def _staged(self, plan) -> None:
        self.banner.ready(plan.version)

    # -------------------------------------------------------------- restart

    def restart_now(self) -> None:
        waiting = update.pending()
        if not waiting:
            self.banner.setVisible(False)
            return
        if QtWidgets.QMessageBox.question(
                self.host, "Restart DATUM",
                "Restart now to finish updating to DATUM %s?" % waiting,
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
                ) != QtWidgets.QMessageBox.Yes:
            return
        # offers to save anything unsaved, and stops if the answer is Cancel
        if not self.host._confirm_discard():
            return
        # If a previous attempt left a log behind, the swap did not
        # finish, and saying so beats offering the same restart again as
        # if nothing had happened.
        said = swap.last_failure()
        if said:
            QtWidgets.QMessageBox.warning(
                self.host, "The last update did not finish",
                "DATUM tried this before and could not complete it."
                "\n\n%s\n\nThe full log is beside the installation "
                "in datum-update-log.txt.  Trying again now."
                % said.splitlines()[-1][:300])
        try:
            swap.apply()
        except update.UpdateError as exc:
            self.banner.problem(str(exc))
            return
        # close() rather than quit(), so closeEvent runs and the SpaceMouse
        # gives its device back.  The swap script is sitting there watching
        # for this process to disappear, so leaving anything holding it
        # open would strand the update rather than break it.
        self.host._closing_for_update = True
        self.host.close()

    # ---------------------------------------------------------------- extras

    def skip_this(self) -> None:
        if self.release is not None:
            self.settings.setValue(SETTINGS_SKIPPED, self.release.version)
        self.banner.setVisible(False)

    def show_notes(self) -> None:
        if self.release is None:
            return
        box = QtWidgets.QMessageBox(self.host)
        box.setWindowTitle("DATUM %s" % self.release.version)
        box.setText("<b>DATUM %s</b>%s" % (
            self.release.version,
            ("  released %s" % self.release.released)
            if self.release.released else ""))
        box.setInformativeText(self.release.notes or "No notes were given.")
        box.setStandardButtons(QtWidgets.QMessageBox.Close)
        box.exec()

    def discard_staged(self) -> None:
        swap.discard()
        self.banner.setVisible(False)
