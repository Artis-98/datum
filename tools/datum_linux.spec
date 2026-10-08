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

# The machine's own graphics stack, and everything a current Mesa driver
# pulls in under it, must come from the machine.  Bundled from the Ubuntu
# 22.04 builder, an older libstdc++, libdrm, zlib or zstd sits first on the
# library path, and the radeonsi or iris driver of a newer distribution
# fails to load against it: the window then has no OpenGL and the 3D view
# cannot start, which is what CachyOS reported (GitHub #3).  These are all
# present wherever there is a desktop with OpenGL, and they only ever grow
# newer symbols, never lose old ones, so the system's copy always serves.
SYSTEM_LIBS = (
    "libGL.so", "libGLX", "libGLdispatch", "libEGL", "libOpenGL",
    "libGLU", "libgbm", "libdrm", "libglapi",
    "libstdc++.so", "libgcc_s.so",
    "libz.so", "libzstd.so", "libexpat.so", "libelf",
    "libX11.so", "libX11-xcb", "libXext", "libXfixes", "libXxf86vm",
    "libXrender", "libXdamage", "libXrandr", "libxshmfence",
    "libxcb.so", "libxcb-dri", "libxcb-glx", "libxcb-present",
    "libxcb-sync", "libxcb-xfixes", "libxcb-randr", "libxcb-shm",
    "libwayland-",
)


def _from_the_system(entry) -> bool:
    name = os.path.basename(entry[0])
    return name.startswith(SYSTEM_LIBS)


dropped = sorted(os.path.basename(e[0]) for e in a.binaries
                 if _from_the_system(e))
print("left to the system: %s" % ", ".join(dropped) if dropped
      else "left to the system: nothing was bundled")
a.binaries = [e for e in a.binaries if not _from_the_system(e)]

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
