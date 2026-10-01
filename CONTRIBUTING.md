# Contributing to DATUM

Thanks for wanting to help. DATUM is an Inventor style parametric CAD built
by one person in the evenings, so every bug report, test and pull request
makes a real difference.

## What helps most

- **Bug reports** with the steps to get there. A small `.pdat` that shows
  the problem is worth more than any description of it.
- **Inventor behaviour we got wrong.** The goal is for an Inventor user to
  feel at home, so "Inventor does X here and DATUM does Y" is a perfectly
  good issue on its own.
- **Pull requests** for anything on the
  [Coming next](https://datum.iiteg.com/changelog.html) list, or anything
  you have opened an issue for first. Ask before starting something large,
  so nobody builds the same thing twice.

Issues and pull requests go to
[github.com/Artis-98/datum](https://github.com/Artis-98/datum).

## Getting it running

You need Python 3.14. DATUM is developed on Windows; Linux should mostly
work from source but is not tested yet, and reports from Linux are very
welcome.

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install cadquery-ocp PySide6 numpy
.venv\Scripts\python.exe datum.py
```

`hidapi` and `comtypes` are optional and only needed for a SpaceMouse.

The README's [How it is put together](README.md#how-it-is-put-together)
section maps every module. In short, `datum/core` is the model and never
imports Qt, and `datum/ui` is the window around it.

## Tests

Run the whole suite before you open a pull request:

```bash
.venv\Scripts\python.exe tests\run.py
```

While working, run only what you touched. A word picks the suites whose
names contain it, and `--fast` skips every suite that needs a window:

```bash
.venv\Scripts\python.exe tests\run.py sketch
.venv\Scripts\python.exe tests\run.py --fast
```

Every change comes with a test. Look at a suite next to what you changed
and copy its shape: a `check(name, condition, extra)` per fact, each name a
plain sentence saying what should be true, and a non-zero exit if any
fail. UI suites build a real `MainWindow` and drive it the way a user
would. `tests/harness.py` answers every message box and file dialog, so a
test never stops to ask; read its docstring before you test anything that
asks the user a question.

A good test fails without your change. It is worth checking that once.

## How the code is written

- **Comments say why, not what.** If a line is there because of a trap in
  OpenCASCADE, Qt or Windows, write the trap down next to it. Most of the
  existing comments are exactly that, and they are the reason the code can
  be changed safely.
- **Match the file you are in**: its naming, its density of comments, its
  idioms. A change should read as if it was always there.
- **Errors say what to do.** A feature that fails tells the user why in
  words they can act on ("pick the plane to extrude to"), never a Python
  exception.
- **Inventor is the reference** for how a command behaves. Where DATUM does
  something differently on purpose, the README says so.

### Line endings and encodings

`.gitattributes` stores every file exactly as it is, on purpose. Some files
are CRLF, some are LF, and `datum/ui/main_window.py` is UTF-8 with a BOM.
Keep each file the way you found it. An editor that silently converts a
file turns a one line change into a diff of the whole file, and we cannot
review that. If your editor has an "EditorConfig" or "keep line endings"
setting, turn it on.

## Pull requests

- One change per pull request, with its tests.
- Say what it does from the user's side in the description: what they
  click, what they see.
- Do not bump the version or touch `release/`; releases are made with
  `tools/release.py` and signed with a key that never leaves the
  maintainer's machine.
- By contributing you agree that your work is released under the
  [MIT licence](LICENSE), like the rest of DATUM.

Not sure about something? Open an issue and ask. There are no silly
questions here.
