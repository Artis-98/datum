# PyInstaller build for DATUM.  Run it through tools/release.py, not by
# hand, so the version written into the executable matches the tag.
#
# Two things here are not boilerplate and will break the build if they are
# removed.
#
# The first is the VTK bundle.  cadquery-ocp links OpenCASCADE's VTK
# bridge into one monolithic OCP.pyd, so the VTK DLLs have to be present
# even though DATUM never imports VTK and has no use for it: with them
# absent, OCP fails at import with "DLL load failed" and the application
# does not start.  They also have to keep their folder names, because
# OCP/__init__.py calls os.add_dll_directory on vtk.libs and
# cadquery_ocp.libs by name and throws if those folders are not there.
# PyInstaller cannot see any of this, because nothing imports them: the
# dependency is a link, not an import.
#
# The second is the exclude list.  PySide6 ships around forty modules and
# DATUM imports five of them, so most of that is dead weight worth several
# hundred megabytes.  vtkmodules is the Python half of VTK, which really
# is unused and really can go; only the DLLs are needed.

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs

ROOT = Path(SPECPATH).parent
SITE = Path(sys.prefix) / "Lib" / "site-packages"
VERSION = os.environ.get("DATUM_VERSION", "0.0.0")


def dll_folder(name):
    """Every DLL in one of the wheel-repair folders, keeping the folder."""
    folder = SITE / name
    if not folder.is_dir():
        raise SystemExit(
            "%s is missing from the environment.  OCP will not load "
            "without it; install cadquery-ocp before building." % name)
    return [(str(p), name) for p in folder.glob("*.dll")]


binaries = dll_folder("vtk.libs") + dll_folder("cadquery_ocp.libs")
binaries += collect_dynamic_libs("OCP")

datas = [
    (str(ROOT / "datum" / "templates"), "datum/templates"),
    (str(ROOT / "datum" / "ui" / "assets"), "datum/ui/assets"),
]

# Qt modules DATUM actually uses: Core, Gui, Widgets, Svg, PrintSupport.
# Everything else in PySide6 goes.  WebEngine alone is a few hundred
# megabytes and would be downloaded again by anybody updating.
excluded_qt = [
    "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DRender",
    "PySide6.QtBluetooth", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtDBus", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtHttpServer", "PySide6.QtLocation", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtNetworkAuth", "PySide6.QtNfc",
    "PySide6.QtPositioning", "PySide6.QtQml", "PySide6.QtQuick",
    "PySide6.QtQuick3D", "PySide6.QtQuickControls2", "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSensors",
    "PySide6.QtSerialBus", "PySide6.QtSerialPort", "PySide6.QtSpatialAudio",
    "PySide6.QtSql", "PySide6.QtStateMachine", "PySide6.QtTest",
    "PySide6.QtTextToSpeech", "PySide6.QtUiTools", "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets", "PySide6.QtWebSockets",
    "PySide6.QtXml", "PySide6.QtConcurrent", "PySide6.QtGraphs",
    "PySide6.QtPdfWidgets", "PySide6.QtNetwork",
]

excludes = excluded_qt + [
    "vtkmodules", "vtk",              # the DLLs stay, the Python does not
    "matplotlib", "PIL", "pandas", "scipy", "IPython", "jupyter",
    "tkinter", "unittest", "pydoc", "doctest", "pytest",
    "PyInstaller", "setuptools", "pip",
]

a = Analysis(
    [str(ROOT / "datum.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        # cryptography is used only through a late import when a release
        # signature is checked, so nothing static points at it
        "cryptography.hazmat.primitives.asymmetric.ed25519",
    ],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DATUM",
    debug=False,
    strip=False,
    upx=False,                 # UPX changes every byte, which would make
                               # every update a full download
    console=False,
    icon=str(ROOT / "tools" / "datum.ico")
    if (ROOT / "tools" / "datum.ico").exists() else None,
    version=str(ROOT / "build" / "version-info.txt")
    if (ROOT / "build" / "version-info.txt").exists() else None,
)

# One directory, never one file.  A one-file build unpacks the whole
# seven hundred megabytes into the temp folder on every single launch,
# and it makes per-file updates impossible: there would be one enormous
# file, and every release would change all of it.
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="DATUM",
)
