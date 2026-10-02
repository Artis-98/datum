"""The Bill of Materials picture for the website: the excavator, counted.

A copy of the excavator example, each part given a part number and a
description and saved, so it carries its picture, then the assembly with
its Bill of Materials open on Parts Only.

    python tests/shot_site_bom.py [output.png]
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-shots"

from PySide6 import QtCore, QtGui, QtWidgets                       # noqa: E402

from datum.ui import bom_ui, icons                                 # noqa: E402
from datum.ui.main_window import MainWindow                        # noqa: E402
from datum.ui.theme import stylesheet                              # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    ROOT, "site", "img", "bom.png")

PARTS = {
    "Boom": ("EX-101", "Main boom, welded box section"),
    "Arm": ("EX-102", "Dipper arm"),
    "Bucket": ("EX-103", "Digging bucket, 300 mm"),
    "Bucket Tooth": ("EX-104", "Bucket tooth, bolt on"),
    "Bucket Link": ("EX-105", "Bucket link"),
    "Idler Link": ("EX-106", "Idler link"),
    "Ram Barrel": ("EX-110", "Hydraulic ram barrel"),
    "Ram Rod": ("EX-111", "Hydraulic ram rod"),
    "Bucket Ram Barrel": ("EX-112", "Bucket ram barrel"),
    "Bucket Ram Rod": ("EX-113", "Bucket ram rod"),
    "Ram Pin": ("EX-114", "Ram pin, 20 mm"),
    "Pivot Pin": ("EX-115", "Pivot pin, 30 mm"),
    "House": ("EX-120", "Upper house"),
    "Cab Frame": ("EX-121", "Cab frame"),
    "Cab Glazing": ("EX-122", "Cab glazing, polycarbonate"),
    "Seat": ("EX-123", "Operator seat"),
    "Counterweight": ("EX-124", "Counterweight, cast"),
    "Exhaust Stack": ("EX-125", "Exhaust stack"),
    "Work Light": ("EX-126", "Work light, LED"),
    "Slew Ring": ("EX-130", "Slew ring bearing"),
    "Track Frame": ("EX-131", "Track frame"),
    "Rubber Track": ("EX-132", "Rubber track, 230 mm"),
    "Drive Sprocket": ("EX-133", "Drive sprocket"),
    "Idler Wheel": ("EX-134", "Idler wheel"),
    "Dozer Blade": ("EX-135", "Dozer blade"),
    "Blade Arm": ("EX-136", "Dozer blade arm"),
}

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
app.setWindowIcon(icons.app_icon())
win = MainWindow()
win.resize(1600, 958)
win.show()


def settle(ms=500):
    timer = QtCore.QElapsedTimer()
    timer.start()
    while timer.elapsed() < ms:
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 20)


def go():
    work = os.path.join(tempfile.mkdtemp(prefix="datum_site_bom_"),
                        "excavator")
    shutil.copytree(os.path.join(ROOT, "examples", "excavator"), work)
    for name, (number, description) in PARTS.items():
        path = os.path.join(work, name + ".pdat")
        if not os.path.exists(path):
            continue
        win.open_path(path)
        settle(250)
        win.viewport.set_view("iso")
        win.viewport.fit_all()
        settle(150)
        win.document.properties.update({"PartNumber": number,
                                        "Description": description})
        win.save_document()
        settle(100)
        # saved with its picture; its tab is not wanted in the picture
        win.close_entry(win.session.active)
        settle(100)
    win.open_path(os.path.join(work, "Excavator.adat"))
    settle(2500)
    win.viewport.set_view("iso")
    win.viewport.fit_all()
    settle(800)

    dialog = bom_ui.BomDialog(win, win.assembly, win.session,
                              win.assembly.library)
    dialog.tabs.setCurrentIndex(1)
    dialog.resize(1080, 640)
    dialog.show()
    settle(400)
    dialog.move(win.mapToGlobal(QtCore.QPoint(
        (win.width() - dialog.width()) // 2 + 120,
        (win.height() - dialog.height()) // 2 + 30)))
    settle(400)

    # the 3D view renders on its own; the window's grab leaves it black
    view_path = OUT + ".view.png"
    win.viewport.grab_image(view_path)
    picture = win.grab()
    painter = QtGui.QPainter(picture)
    rendered = QtGui.QImage(view_path)
    if not rendered.isNull():
        origin = win.viewport.mapTo(win, QtCore.QPoint(0, 0))
        painter.drawImage(QtCore.QRect(origin.x(), origin.y(),
                                       win.viewport.width(),
                                       win.viewport.height()), rendered)
    painter.drawPixmap(win.mapFromGlobal(dialog.mapToGlobal(
        QtCore.QPoint(0, 0))), dialog.grab())
    painter.end()
    try:
        os.remove(view_path)
    except OSError:
        pass
    picture.save(OUT)
    print("saved", OUT, picture.width(), "x", picture.height())
    app.quit()


QtCore.QTimer.singleShot(1600, go)
sys.exit(app.exec())
