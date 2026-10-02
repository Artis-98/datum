# PyInstaller build for DATUM on Linux, the preview build.
#
# The Windows build (datum.spec) copies the VTK and OCP DLL folders in by
# hand, because on Windows OCP's link to VTK is invisible to PyInstaller.
# On Linux the wheels are repaired by auditwheel, every library OCP needs
# sits in cadquery_ocp.libs and vtk's own folder with an RPATH pointing at
# it, and PyInstaller follows those links the way ldd does.  So this is the
# same build without the hand copying: the same data, the same excludes.
#
# Built by .github/workflows/linux.yml on an Ubuntu runner.

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs

ROOT = Path(SPECPATH).parent

binaries = collect_dynamic_libs("OCP")

datas = [
    (str(ROOT / "datum" / "templates"), "datum/templates"),
    (str(ROOT / "datum" / "ui" / "assets"), "datum/ui/assets"),
]

excluded_qt = [
    "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DRender",
    "PySide6.QtBluetooth", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtDesigner", "PySide6.QtHelp",
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
    "vtkmodules", "vtk",              # the libraries stay, the Python goes
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
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="DATUM",
)
