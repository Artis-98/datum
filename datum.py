"""Launch DATUM."""

import sys

from datum.app import main

if __name__ == "__main__":
    import os, sys
    code = main()
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(code or 0)
