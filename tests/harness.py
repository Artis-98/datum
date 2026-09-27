"""Keep a test run from ever stopping to ask a question.

A test that puts a modal on screen stops being a test: it sits there waiting
for a human to click Save, Discard or Cancel, and the run is stuck until
somebody does.  Importing this module replaces every modal the application
can raise with one that answers itself and writes down what it was asked.

    import harness            # noqa: F401  - installs on import

Nothing else is needed for a test to be safe.  The defaults are chosen to be
the least destructive thing that keeps going:

    question        Discard if it is offered, else Yes, else Ok
    warning /
    information /
    critical        Ok
    file dialogs    cancelled, so nothing is read or written by accident
    input dialogs   cancelled
    modal exec()    rejected rather than shown

A test that cares about the answer sets it, and can read back what was
asked:

    harness.answer(question=QtWidgets.QMessageBox.Yes)
    harness.saving("C:/tmp/part.pdat")
    harness.opening([a, b])
    ...
    assert "cannot place itself" in harness.text("warning")

A test that stubs one of these itself still wins - it simply replaces what
was installed here, and restoring it afterwards restores this rather than
the blocking original.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from PySide6 import QtWidgets

# ---------------------------------------------------------------- recording

#: every modal that was raised, in order: (kind, title, text)
shown: List[Tuple[str, str, str]] = []

_answers: Dict[str, Any] = {}
_files: Dict[str, Any] = {}

KINDS = ("question", "warning", "information", "critical", "about")


def reset() -> None:
    """Forget what was asked, and go back to the default answers."""
    shown.clear()
    _answers.clear()
    _files.clear()


def answer(**kwargs) -> None:
    """Set the reply to a kind of message box, e.g. ``question=...``."""
    _answers.update(kwargs)


def saving(path: str = "", selected: str = "") -> None:
    """What the next Save As dialog returns.  Empty means cancelled."""
    _files["save"] = (path, selected)


def opening(paths, selected: str = "") -> None:
    """What the next Open dialog returns - one path or a list of them."""
    if isinstance(paths, (list, tuple)):
        _files["open_many"] = (list(paths), selected)
        _files["open_one"] = (paths[0] if paths else "", selected)
    else:
        _files["open_one"] = (paths, selected)
        _files["open_many"] = ([paths] if paths else [], selected)


def directory(path: str = "") -> None:
    _files["directory"] = path


def typing(text: str = "", accepted: bool = True) -> None:
    """What the next text-input dialog returns."""
    _files["text"] = (text, accepted and bool(text))


def of_kind(kind: str) -> List[str]:
    """Every message of one kind that has been raised, oldest first."""
    return [text for seen, _title, text in shown if seen == kind]


def text(kind: Optional[str] = None) -> str:
    """The most recent message, of one kind or of any."""
    for seen, _title, body in reversed(shown):
        if kind is None or seen == kind:
            return body
    return ""


def asked(kind: Optional[str] = None) -> bool:
    return bool(of_kind(kind) if kind else shown)


class answering:
    """Answer one way for the length of a ``with`` block, then go back."""

    def __init__(self, **kwargs) -> None:
        self._wanted = kwargs
        self._before: Dict[str, Any] = {}

    def __enter__(self) -> "answering":
        self._before = dict(_answers)
        _answers.update(self._wanted)
        return self

    def __exit__(self, *_exc) -> None:
        _answers.clear()
        _answers.update(self._before)


# ------------------------------------------------------------- the answers


def _default_question(buttons) -> Any:
    """The least destructive reply that still lets the run carry on."""
    box = QtWidgets.QMessageBox
    if buttons is None:
        return box.Yes
    for wanted in (box.Discard, box.Yes, box.Ok, box.Close):
        if buttons & wanted:
            return wanted
    return box.Ok


def _record(kind: str, args) -> Tuple[str, str]:
    """Message boxes are called (parent, title, text, buttons, ...)."""
    title = str(args[1]) if len(args) > 1 else ""
    body = str(args[2]) if len(args) > 2 else ""
    shown.append((kind, title, body))
    return (title, body)


def _make(kind: str):
    def handler(*args, **kwargs):
        _record(kind, args)
        if kind in _answers:
            return _answers[kind]
        if kind == "question":
            buttons = args[3] if len(args) > 3 else kwargs.get("buttons")
            return _default_question(buttons)
        return QtWidgets.QMessageBox.Ok

    return staticmethod(handler)


def install() -> None:
    """Replace every modal with one that answers itself.

    And keep the run out of the real projects. A test opens and saves
    files, and every one lands in the active project's recent list; run
    against the real Documents folder that list filled with temp files
    and pushed out what somebody had actually been working on.
    """
    import os
    import tempfile

    os.environ.setdefault("DATUM_SETTINGS_ORG", "IITEG-tests")
    # The window's tests run everything in their own process, as they
    # always have, so what they check is the model and not the timing of
    # other processes. The workers have tests of their own.
    os.environ.setdefault("DATUM_NO_WORKERS", "1")
    os.environ.setdefault("DATUM_DOCUMENTS",
                          tempfile.mkdtemp(prefix="datum_documents_"))

    box = QtWidgets.QMessageBox
    for kind in KINDS:
        setattr(box, kind, _make(kind))

    dialog = QtWidgets.QFileDialog

    def save_name(*_args, **_kwargs):
        return _files.get("save", ("", ""))

    def open_name(*_args, **_kwargs):
        return _files.get("open_one", ("", ""))

    def open_names(*_args, **_kwargs):
        return _files.get("open_many", ([], ""))

    def existing(*_args, **_kwargs):
        return _files.get("directory", "")

    dialog.getSaveFileName = staticmethod(save_name)
    dialog.getOpenFileName = staticmethod(open_name)
    dialog.getOpenFileNames = staticmethod(open_names)
    dialog.getExistingDirectory = staticmethod(existing)

    def get_text(*_args, **_kwargs):
        return _files.get("text", ("", False))

    QtWidgets.QInputDialog.getText = staticmethod(get_text)

    # A dialog that runs its own event loop would hang the run outright.
    # The application's own feature dialogs are shown, not exec'd, so this
    # only catches the ones that would otherwise block - and rejecting is
    # the same as the user closing them.
    QtWidgets.QDialog.exec = lambda self, *a, **k: QtWidgets.QDialog.Rejected
    QtWidgets.QDialog.exec_ = QtWidgets.QDialog.exec


install()
