"""dLogic in the window: the panel, the editors, the triggers.

The core holds the promises about what code may do. These hold the ones
about what a person sees: that a file with code in it says so the moment
it opens, that Allow builds what could not be built, that the editor
lists what is there and lights up the line that failed, and that a code
feature can be written from nothing inside the application.
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WORK = tempfile.mkdtemp(prefix="datum_dlogic_ui_")
os.environ["DATUM_CONFIG_DIR"] = os.path.join(WORK, "cfg")

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from datum.core import geometry, rules  # noqa: E402
from datum.core.features import CodeFeature  # noqa: E402
from datum.ui import rules_ui  # noqa: E402
from datum.ui.main_window import MainWindow  # noqa: E402
from datum.ui.theme import stylesheet  # noqa: E402

FAILS = []
app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())

win = MainWindow()
win.resize(1400, 880)
win.show()
app.processEvents()


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def pump(n=4):
    for _ in range(n):
        app.processEvents()


SAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))), "examples", "railing", "Stair Railing.pdat")


print("a file with code in it says so when it opens")

copy = os.path.join(WORK, "Stair Railing.pdat")
shutil.copy(SAMPLE, copy)
check("the stair sample opens", win.open_path(copy))
pump()
doc = win.document
panel = win.rules_panel
check("its code features have not run",
      all("trusted" in f.error for f in doc.features
          if isinstance(f, CodeFeature)),
      [f.error for f in doc.features])
check("the dLogic panel opened by itself", win.rules_dock.isVisible())
check("  on this document", panel._document is doc)
check("  with the warning showing", panel.banner.isVisible())
check("  naming what it carries",
      "2 rules" in panel.banner_text.text()
      and "2 code features" in panel.banner_text.text(),
      panel.banner_text.text())
check("the status bar says why", "not been allowed" in
      win.status_message.text(), win.status_message.text())
check("the rule marked as a button is a button",
      panel.forms.isVisible()
      and [panel.forms_layout.itemAt(i).widget().text()
           for i in range(panel.forms_layout.count())] == ["Size"])
labels = []
root = panel.tree.topLevelItem(0)
for i in range(root.childCount()):
    labels.append(root.child(i).text(0))
check("rules show when they run",
      any("when rise, going, steps change" in t for t in labels), labels)
check("code features are listed with the rules",
      any("Railing" in t and "code feature" in t for t in labels), labels)

panel.trust()
pump()
check("Allow builds what could not be built",
      doc.last_report.ok and not any(f.error for f in doc.features),
      [f.error for f in doc.features if f.error])
check("  and the warning goes", not panel.banner.isVisible())
check("  and the shape is there", doc.shape is not None
      and geometry.Solid(doc.shape).size[2] > 2000)


print()
print("a watched parameter sets its rule off")

doc.properties.pop("Description", None)
doc.params.set_expression("steps", "10")
win.rebuild(keep_camera=True)
pump()
check("changing steps ran the rule watching it",
      "10 steps" in doc.properties.get("Description", ""),
      doc.properties.get("Description"))


print()
print("before saving")

stamp = doc.rules.add("Stamp", "props.Revision = 'C'")
stamp.on_rebuild = False
stamp.before_save = True
harness.saving(os.path.join(WORK, "saved.pdat"))
win.save_document(as_new=True)
check("a before-save rule ran", doc.properties.get("Revision") == "C")
check("  and what it set was saved", os.path.exists(
    os.path.join(WORK, "saved.pdat")))


print()
print("the rule editor")

rule = rules.Rule(name="Broken", source="x = 1\ny = params.nothing_here\n")
editor = rules_ui.RuleEditor(rule, doc, win)
editor.show()
pump()
groups = [editor.script.reference.tree.topLevelItem(i).text(0)
          for i in range(editor.script.reference.tree.topLevelItemCount())]
check("the side lists what the document has",
      groups[:2] == ["Parameters", "Features"], groups)
check("  and the commands", "Ask with a window" in groups
      and "Drive the model" in groups, groups)
params = editor.script.reference.tree.topLevelItem(0)
names = [params.child(i).text(0) for i in range(params.childCount())]
check("  parameters with their values", "steps = 10" in names, names)

editor.run_now()
pump()
check("a failure lights up its line", editor.editor.error_line == 2,
      editor.editor.error_line)
check("  and says what went wrong", "nothing_here" in
      editor.output.toPlainText(), editor.output.toPlainText())
editor.editor.insertPlainText("# typing clears the mark\n")
check("typing clears the mark", editor.editor.error_line == 0)

editor.editor.setPlainText("")
editor.editor.insert_snippet("params.width = 100\n")
check("a double-clicked entry lands in the script",
      editor.editor.toPlainText() == "params.width = 100\n")
editor.script.reference.search.setText("sketch dim")
tree = editor.script.reference.tree
shown = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())
         if not tree.topLevelItem(i).isHidden()]
check("the search narrows it down", shown == ["Drive the model"], shown)

words = rules_ui.completion_words(doc, "rule", "params")
check("names are offered after params.", "steps" in words and
      "rail_height" in words)
check("  and the toolkit in a code feature",
      "rod" in rules_ui.completion_words(doc, "code", None))
editor.on_open.setChecked(True)
editor.watch.setText("steps, rise")
editor.button_box.setChecked(True)
editor.gather()
check("the triggers are kept", rule.on_open and rule.watch == [
    "steps", "rise"] and rule.button)
editor.close()


print()
print("a code feature, written in the application")

win.new_document()
pump()
part = win.document
win.new_code_feature()
pump()
dialog = win._active_dialog
check("Code Feature opens its editor",
      isinstance(dialog, rules_ui.CodeDialog), type(dialog).__name__)
feature = dialog.feature
pump(6)
check("the starter builds something", part.shape is not None
      and not feature.error, feature.error)
dialog.script.editor.setPlainText(
    "a = box(10, 10, 10)\nresult(a.move(nowhere))\n")
dialog.preview()
pump()
check("a failing script lights up its line",
      dialog.script.editor.error_line == 2, dialog.script.editor.error_line)
check("  and OK is refused while it fails", not dialog.ok_button.isEnabled())
dialog.script.editor.setPlainText(
    "log('building')\nresult(rod((0, 0, 0), (0, 0, 300), 12))\n")
dialog.name_edit.setText("Pin")
dialog.commit()
pump()
check("OK keeps it", feature in part.features and feature.name == "Pin")
check("  and it built", part.last_report.ok and
      abs(geometry.Solid(part.shape).size[2] - 300) < 1)
check("  and what it logged is kept to show", feature.output == "building")
esc = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Escape,
                      QtCore.Qt.NoModifier)
win.edit_feature(feature.id)
pump()
again = win._active_dialog
check("double-click opens it again", isinstance(again, rules_ui.CodeDialog))
again.keyPressEvent(esc)
pump()
check("Escape does not throw the script away",
      win._active_dialog is again and again.isVisible())
again.cancel()
pump()


print()
print("a form starts where the model is")

part.params.add("size", "2")
form = rules_ui.FormDialog(win, part, "Pick", [
    rules.choice("size", ["1", "2", "3"])])
combo = form.widgets["size"]
check("a choice shows the value the model has", combo.currentText() == "2",
      combo.currentText())
form.close()


print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
