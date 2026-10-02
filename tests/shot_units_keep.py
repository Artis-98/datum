"""Screenshot dProperties with its units, and the question changing them asks."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401

from PySide6 import QtWidgets  # noqa: E402

from datum.ui.main_window import MainWindow  # noqa: E402
from datum.ui.theme import stylesheet  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "."
os.makedirs(OUT, exist_ok=True)

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
win = MainWindow()
win.resize(1500, 950)
win.show()
win.new_document(prompt=False)
for _ in range(5):
    app.processEvents()

win.show_properties()
for _ in range(5):
    app.processEvents()
win.properties_window.grab().save(os.path.join(OUT, "01_dproperties.png"))
print("shot: dproperties")


def grab_and_cancel(box):
    box.show()
    for _ in range(5):
        app.processEvents()
    box.grab().save(os.path.join(OUT, "02_units_question.png"))
    print("shot: units question")
    box.hide()
    return 0


QtWidgets.QMessageBox.exec = grab_and_cancel
win._ask_unit_change(win.document, "mm", "in")
