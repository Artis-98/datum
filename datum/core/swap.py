"""Putting a staged update in place once DATUM has closed.

Windows will let you rename a running executable but it will not let you
touch a DLL that is loaded, and an installed DATUM has Qt, OpenCASCADE and
the Python runtime all mapped into the process.  So the files cannot be
replaced by the application that is using them.  Something else has to do
it, after that application has gone.

That something is a batch script.  It is not elegant, but it is the only
helper that is guaranteed to exist on every Windows machine and that does
not itself live among the files being replaced.  A second executable would
have to be excluded from its own update; a script copied to the temp
folder simply is not there when the move happens.

The script waits for DATUM's process id to disappear, deletes whatever the
release dropped, moves the staged files over the installation in one pass,
and starts DATUM again.  If it cannot finish, it leaves the staging folder
alone: DATUM finds it on the next start and offers to apply it again,
which is safe because everything in there has already been checked.
"""

from __future__ import annotations

import ntpath
import os
import subprocess
import sys
import tempfile
from typing import Dict, Optional, Sequence

from . import update

# So the console window never flashes up in the person's face.
#
# CREATE_NO_WINDOW on its own, and never with DETACHED_PROCESS: Windows
# treats those two and CREATE_NEW_CONSOLE as mutually exclusive, and
# passing both does not mean "extra hidden", it means the script silently
# never runs. That is exactly what it did - the update prepared itself,
# DATUM closed to let it work, a console blinked, and nothing was
# replaced. DETACHED_PROCESS is left defined because it is worth naming
# the thing not to use.
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008          # do not combine with the above

# Robocopy says 0 to 7 for "worked"; 8 and above is a real failure.
ROBOCOPY_OK = 8


def log_for(target: str) -> str:
    """Where the helper writes when it cannot finish.

    Beside the installation rather than in the temp folder, so that
    somebody asked "what did it say" has somewhere to look that they can
    find without being told what %TEMP% means.
    """
    return os.path.join(os.path.abspath(target), "datum-update-log.txt")


def script_for(target: str, staging: str, executable: str, pid: int,
               delete: Sequence[str] = (), relaunch: bool = True) -> str:
    """The batch script that performs the swap.

    Built as a string by a function of its own so it can be read and
    tested, rather than being assembled inline at the moment it is needed
    and only ever running on somebody's machine.
    """
    log = log_for(target)
    lines = [
        "@echo off",
        "setlocal EnableExtensions",
        "title DATUM update",
        'set "LOG=%s"' % log,
        '> "%LOG%" echo DATUM update, target ' + target,
        "",
        "rem Wait for DATUM to close.  Its files cannot be replaced while",
        "rem the process still has them open.",
        'set "TRIES=0"',
        ":wait",
        'tasklist /FI "PID eq %d" /NH 2>nul | find "%d" >nul' % (pid, pid),
        "if errorlevel 1 goto ready",
        "set /a TRIES+=1",
        "if %TRIES% GTR 150 goto giveup",
        "ping -n 2 127.0.0.1 >nul",
        "goto wait",
        "",
        ":ready",
    ]

    if delete:
        lines.append("rem Files this release no longer has.")
        for relative in delete:
            # a batch file's paths are Windows paths wherever it is written
            full = ntpath.join(target, relative.replace("/", "\\"))
            lines.append('del /f /q "%s" 2>nul' % full)
        lines.append("")

    lines += [
        "rem The staged files, checked already, moved over in one pass.",
        'del /f /q "%s" 2>nul' % ntpath.join(staging, update.PLAN_FILE),
        'robocopy "%s" "%s" /E /MOVE /NFL /NDL /NP /R:2 /W:1 '
        '/LOG+:"%%LOG%%"'
        % (staging.rstrip("\\/"), target.rstrip("\\/")),
        'echo robocopy said %errorlevel% >> "%LOG%"',
        "if errorlevel %d goto failed" % ROBOCOPY_OK,
        'rd /s /q "%s" 2>nul' % staging.rstrip("\\/"),
    ]

    if relaunch:
        lines += ["", 'start "" "%s"' % executable]
    lines += [
        'del /f /q "%LOG%" 2>nul',
        "goto done",
        "",
        ":giveup",
        'echo DATUM never closed, so nothing was changed. >> "%LOG%"',
        "rem DATUM never closed.  Leave everything alone; it will be",
        "rem offered again next time it starts.",
        "goto done",
        "",
        ":failed",
        "rem Part-applied.  The staging folder is deliberately left in",
        "rem place so the next start can finish the job, and the log is",
        "rem left with it, because a swap that quietly does nothing is the",
        "rem worst way for this to fail.",
        'echo The update did not finish. >> "%LOG%"',
        "goto done",
        "",
        ":done",
        "rem Delete this script now that it has run.",
        '(goto) 2>nul & del "%~f0"',
        "",
    ]
    return "\r\n".join(lines)


def ready(target: Optional[str] = None) -> Optional[Dict]:
    """The staged update waiting to be applied, if any."""
    folder = target or update.install_dir()
    plan = update.read_plan(folder)
    if not plan or not plan.get("version"):
        return None
    return plan


def apply(target: Optional[str] = None, executable: Optional[str] = None,
          relaunch: bool = True, pid: Optional[int] = None) -> str:
    """Launch the helper and hand it the job.  The caller then exits.

    Returns the path of the script, mostly so a caller can say where it
    went if something goes wrong.

    ``pid`` is the process the helper waits to see the back of, and is
    this one unless a caller says otherwise. It is a parameter so that a
    test can drive this function rather than a hand-made copy of it: the
    bug that made updates do nothing at all was in the launch, not in the
    script, and a test of the script alone could never have caught it.
    """
    folder = os.path.abspath(target or update.install_dir())
    plan = ready(folder)
    if plan is None:
        raise update.UpdateError("there is no staged update to apply")

    exe = executable or sys.executable
    script = script_for(folder, update.staging_dir(folder), exe,
                        os.getpid() if pid is None else pid,
                        plan.get("delete") or [], relaunch)

    # Into the installation, not the temp folder. Two reasons, and the
    # second is the one that bites: update.KEEP_ALWAYS has always
    # protected "datum-update-" from being swept up by a swap, which only
    # means anything if the script lives here; and a freshly written .cmd
    # run out of %TEMP% by an unsigned application is one of the most
    # commonly blocked patterns there is, which looks from the outside
    # exactly like a console that flashes and does nothing.
    path = os.path.join(folder, "datum-update-%d.cmd" % os.getpid())
    try:
        with open(path, "w", encoding="ascii", errors="replace",
                  newline="") as out:
            out.write(script)
    except OSError:
        handle, path = tempfile.mkstemp(prefix="datum-update-",
                                        suffix=".cmd")
        with os.fdopen(handle, "w", encoding="ascii", errors="replace",
                       newline="") as out:
            out.write(script)

    subprocess.Popen(["cmd.exe", "/c", path],
                     creationflags=CREATE_NO_WINDOW,
                     close_fds=True)
    return path


def last_failure(target: Optional[str] = None) -> str:
    """What the helper said last time, if it left anything behind.

    An update that quietly does nothing is the worst way for this to
    fail, so when a staged update is still sitting there on the next
    start, there is something to read rather than a shrug.
    """
    folder = os.path.abspath(target or update.install_dir())
    log = log_for(folder)
    if not os.path.exists(log):
        return ""
    try:
        with open(log, encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def discard(target: Optional[str] = None) -> None:
    """Throw a staged update away without applying it."""
    update.clear_staging(target or update.install_dir())
