r"""Run the test suites, several at a time.

Almost all of the wall time is Qt and OpenCASCADE starting up: the six core
suites test geometry in about ten seconds between them, while the other
twenty-three each build a whole MainWindow first.  That cost is per process
and cannot be shared, but it can be overlapped, so the suites run in
parallel - they are separate processes that touch separate temp files, and
nothing in them is shared state.

    python tests\run.py              every suite
    python tests\run.py --fast       only the ones with no GUI
    python tests\run.py cam sketch   only suites matching those words
    python tests\run.py -j 4         four at a time instead of the default

Exits nonzero if any suite fails, and prints the output of the ones that
did so nothing is hidden by the parallelism.
"""

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def suites():
    return sorted(os.path.join(HERE, name) for name in os.listdir(HERE)
                  if name.startswith("test_") and name.endswith(".py"))


def is_core(path):
    """A suite with no QApplication in it, so no window to build."""
    try:
        with open(path, encoding="utf-8") as handle:
            return "QApplication" not in handle.read()
    except OSError:
        return False


def run(path):
    started = time.time()
    done = subprocess.run([sys.executable, path], cwd=ROOT,
                          capture_output=True, text=True)
    return (path, done.returncode, time.time() - started,
            done.stdout + done.stderr)


def main(argv):
    args = list(argv)
    workers = 6
    if "-j" in args:
        i = args.index("-j")
        workers = int(args[i + 1])
        del args[i:i + 2]
    fast = "--fast" in args
    if fast:
        args.remove("--fast")

    chosen = suites()
    if fast:
        chosen = [p for p in chosen if is_core(p)]
    if args:
        chosen = [p for p in chosen
                  if any(word in os.path.basename(p) for word in args)]
    if not chosen:
        print("nothing matched %s" % " ".join(args))
        return 1

    # A GUI suite that loses the race for the foreground window is the one
    # thing parallelism can break, so they never outnumber the cores.
    workers = max(1, min(workers, len(chosen), (os.cpu_count() or 4)))
    print("%d suite(s), %d at a time" % (len(chosen), workers))

    started = time.time()
    failed = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for path, code, seconds, output in pool.map(run, chosen):
            name = os.path.basename(path)
            print("  %-4s %6.1fs  %s" % ("FAIL" if code else "ok",
                                         seconds, name))
            if code:
                failed.append((name, output))

    print("-" * 60)
    print("%d suite(s) in %.1fs wall" % (len(chosen), time.time() - started))

    for name, output in failed:
        print()
        print("=" * 60)
        print(name)
        print("=" * 60)
        print(output.strip()[-4000:])

    if failed:
        print()
        print("%d FAILED: %s" % (len(failed), ", ".join(n for n, _ in failed)))
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
