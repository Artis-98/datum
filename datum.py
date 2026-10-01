"""Launch DATUM."""

import sys

from datum.app import main

if __name__ == "__main__":
    code = main()
    if sys.platform == "darwin":
        # On macOS the interpreter's own teardown can crash once Qt has
        # gone, taking the OpenCASCADE view with it (PR #1). The exit
        # handlers run first, which is what stops the worker processes,
        # and then DATUM leaves without the teardown. Windows has no such
        # crash and exits the ordinary way.
        import atexit
        import os
        atexit._run_exitfuncs()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code or 0)
    sys.exit(code)
