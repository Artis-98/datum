"""Write tools/datum.ico from the mark DATUM draws for itself.

The application has no image assets: every icon is drawn at runtime from
code.  Windows, though, wants a real .ico file for the executable, the
installer and the shortcut, so one is generated here from the same
drawing rather than kept as a separate picture that could drift away from
what the application shows.

    python tools/make_icon.py
"""

from __future__ import annotations

import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6 import QtCore, QtGui, QtWidgets          # noqa: E402

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
OUT = os.path.join(ROOT, "tools", "datum.ico")


def png_bytes(size: int) -> bytes:
    from datum.ui import icons

    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    painter.scale(size / icons.GRID, size / icons.GRID)
    icons.draw_mark(painter, field=True)
    painter.end()

    buffer = QtCore.QBuffer()
    buffer.open(QtCore.QIODevice.WriteOnly)
    pixmap.save(buffer, "PNG")
    return bytes(buffer.data())


def write_ico(path: str) -> str:
    """Assemble a PNG-compressed .ico.

    Qt can write .ico but caps it at 256 pixels and drops the larger
    entries silently, so the file is assembled here instead.  Every modern
    Windows reads PNG-compressed icon entries, which keeps a 256 pixel
    icon to a few kilobytes rather than a quarter of a megabyte.
    """
    images = [(size, png_bytes(size)) for size in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)

    directory = b""
    for size, data in images:
        directory += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size,      # 0 means 256
            0 if size >= 256 else size,
            0, 0, 1, 32, len(data), offset)
        offset += len(data)

    with open(path, "wb") as handle:
        handle.write(header)
        handle.write(directory)
        for _size, data in images:
            handle.write(data)
    return path


def main() -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    path = write_ico(OUT)
    print("wrote %s (%d bytes, %d sizes)"
          % (path, os.path.getsize(path), len(SIZES)))
    del app
    return 0


if __name__ == "__main__":
    sys.exit(main())
