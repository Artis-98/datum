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

import os
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Sequence

from . import update

# So the console window never flashes up in the person's face
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008

# Robocopy says 0 to 7 for "worked"; 8 and above is a real failure.
ROBOCOPY_OK = 8


def script_for(target: str, staging: str, executable: str, pid: int,
               delete: Sequence[str] = (), relaunch: bool = True) -> str:
    """The batch script that performs the swap.

    Built as a string by a function of its own so it can be read and
    tested, rather than being assembled inline at the moment it is needed
    and only ever running on somebody's machine.
    """
    lines = [
        "@echo off",
        "setlocal EnableExtensions",
        "title DATUM update",
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
            full = os.path.join(target, relative.replace("/", os.sep))
            lines.append('del /f /q "%s" 2>nul' % full)
        lines.append("")

    lines += [
        "rem The staged files, checked already, moved over in one pass.",
        'del /f /q "%s" 2>nul' % os.path.join(staging, update.PLAN_FILE),
        'robocopy "%s" "%s" /E /MOVE /NFL /NDL /NJH /NJS /NP /R:2 /W:1 >nul'
        % (staging.rstrip("\\/"), target.rstrip("\\/")),
        "if errorlevel %d goto failed" % ROBOCOPY_OK,
        'rd /s /q "%s" 2>nul' % staging.rstrip("\\/"),
    ]

    if relaunch:
        lines += ["", 'start "" "%s"' % executable]
    lines += [
        "goto done",
        "",
        ":giveup",
        "rem DATUM never closed.  Leave everything alone; it will be",
        "rem offered again next time it starts.",
        "goto done",
        "",
        ":failed",
        "rem Part-applied.  The staging folder is deliberately left in",
        "rem place so the next start can finish the job.",
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
          relaunch: bool = True) -> str:
    """Launch the helper and hand it the job.  The caller then exits.

    Returns the path of the script, mostly so a caller can say where it
    went if something goes wrong.
    """
    folder = os.path.abspath(target or update.install_dir())
    plan = ready(folder)
    if plan is None:
        raise update.UpdateError("there is no staged update to apply")

    exe = executable or sys.executable
    script = script_for(folder, update.staging_dir(folder), exe,
                        os.getpid(), plan.get("delete") or [], relaunch)

    handle, path = tempfile.mkstemp(prefix="datum-update-", suffix=".cmd")
    with os.fdopen(handle, "w", encoding="ascii", errors="replace",
                   newline="") as out:
        out.write(script)

    subprocess.Popen(["cmd.exe", "/c", path],
                     creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
                     close_fds=True)
    return path


def discard(target: Optional[str] = None) -> None:
    """Throw a staged update away without applying it."""
    update.clear_staging(target or update.install_dir())
