"""The release pipeline and the updater, checked against each other.

The two halves are written separately and are the obvious place for a
disagreement to hide: release.py lays a release out on disk, update.py
reads it back, and nothing forces them to agree about where anything goes.
So this builds a release the way release.py does, serves it the way a web
server would, and updates a fake installation out of it, all the way
through to the files on disk being the new ones.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datum.core import swap, update                            # noqa: E402

FAILS = []
WORK = tempfile.mkdtemp(prefix="datum_release_")


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def write(folder, relative, text):
    path = os.path.join(folder, relative.replace("/", os.sep))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def read(folder, relative):
    try:
        with open(os.path.join(folder, relative.replace("/", os.sep)),
                  encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


# ==========================================================================
print("a release is laid out the way the updater expects to find it")

# what a build produced
build_0 = os.path.join(WORK, "build-0.2.0")
write(build_0, "DATUM.exe", "binary v0.2.0")
write(build_0, "_internal/Qt6Core.dll", "qt, very large, rarely changes")
write(build_0, "_internal/vtk.libs/vtkCommonCore.dll", "vtk, never changes")
write(build_0, "_internal/datum/core/hlr.py", "hlr v1")
write(build_0, "_internal/datum/ui/theme.py", "theme v1")
write(build_0, "_internal/datum/templates/ISO A3.ddat", "a template")

build_1 = os.path.join(WORK, "build-0.2.1")
shutil.copytree(build_0, build_1)
write(build_1, "DATUM.exe", "binary v0.2.1")
write(build_1, "_internal/datum/core/hlr.py", "hlr v2, now with hatching")
write(build_1, "_internal/datum/core/bom.py", "bom, brand new")
os.remove(os.path.join(build_1, "_internal", "datum", "ui", "theme.py"))


def publish(build, version, server, notes=""):
    """What tools/release.py does, in the same order and shape."""
    folder = os.path.join(server, version)
    os.makedirs(folder, exist_ok=True)
    manifest = update.manifest_of(build)
    manifest["version"] = version
    for relative in manifest["files"]:
        source = os.path.join(build, relative.replace("/", os.sep))
        target = os.path.join(folder, relative.replace("/", os.sep))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(source, target)
    with open(os.path.join(folder, "manifest.json"), "w",
              encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=1, sort_keys=True)
    with open(os.path.join(server, update.FEED_FILE), "w",
              encoding="utf-8") as handle:
        json.dump({"version": version, "released": "2026-09-23",
                   "notes": notes,
                   "manifest": "%s/manifest.json" % version,
                   "minimum": "0.1.0"}, handle, indent=1)
    return folder


server = os.path.join(WORK, "server")
publish(build_0, "0.2.0", server)
publish(build_1, "0.2.1", server, "Hatching on sections.")

check("the feed is at the root", os.path.exists(
    os.path.join(server, update.FEED_FILE)))
check("each version has its own folder with a manifest",
      os.path.exists(os.path.join(server, "0.2.1", "manifest.json")))
check("and the files sit beside it, at their own paths",
      os.path.exists(os.path.join(server, "0.2.1", "_internal", "datum",
                                  "core", "bom.py")))


# ==========================================================================
print("the updater reads that layout without being told anything extra")

BASE = "https://downloads.invalid/datum/"


def serve(url):
    """Stand in for the web server: map a URL back onto the folder."""
    if not url.startswith(BASE):
        raise update.UpdateError("404 %s" % url)
    relative = urllib.parse.unquote(url[len(BASE):])
    path = os.path.join(server, relative.replace("/", os.sep))
    if not os.path.exists(path):
        raise update.UpdateError("404 %s" % url)
    with open(path, "rb") as handle:
        return handle.read()


release = update.check(BASE, fetcher=lambda base, name: serve(base + name))
check("the feed named the newest version", release.version == "0.2.1",
      release.version)
check("with its notes", "Hatching" in release.notes)
check("and it pointed at the manifest",
      release.manifest == "0.2.1/manifest.json", release.manifest)

manifest = json.loads(serve(BASE + release.manifest).decode("utf-8"))
check("the manifest loaded", manifest.get("version") == "0.2.1")
check("and lists every file of the build",
      len(manifest["files"]) == len(update.manifest_of(build_1)["files"]),
      (len(manifest["files"]), len(update.manifest_of(build_1)["files"])))


# ==========================================================================
print("updating a 0.2.0 installation fetches only what moved")

installed = os.path.join(WORK, "installed")
shutil.copytree(build_0, installed)

plan = update.plan_for(manifest, installed, release.version)
check("the two changed files and the new one are fetched",
      sorted(plan.download) == ["DATUM.exe",
                                "_internal/datum/core/bom.py",
                                "_internal/datum/core/hlr.py"],
      sorted(plan.download))
check("Qt is not re-downloaded",
      "_internal/Qt6Core.dll" not in plan.download)
check("nor is VTK, which is most of the install",
      "_internal/vtk.libs/vtkCommonCore.dll" not in plan.download)
check("the dropped module is marked for deletion",
      plan.delete == ["_internal/datum/ui/theme.py"], plan.delete)
check("and the rest is left alone", plan.unchanged == 3, plan.unchanged)

staged = update.download(plan, release, fetcher=serve)
check("everything landed in staging", os.path.isdir(staged))
check("and the installation is still untouched",
      read(installed, "DATUM.exe") == "binary v0.2.0",
      read(installed, "DATUM.exe"))
check("with the update recorded as pending",
      update.pending(installed) == "0.2.1", update.pending(installed))


# ==========================================================================
print("and the swap finishes the job")

if os.name == "nt":
    script = swap.script_for(installed, update.staging_dir(installed),
                             "cmd.exe /c exit", 1, plan.delete,
                             relaunch=False)
    path = os.path.join(WORK, "apply.cmd")
    with open(path, "w", encoding="ascii", newline="") as handle:
        handle.write(script)
    subprocess.run(["cmd.exe", "/c", path], capture_output=True, timeout=120)
    deadline = time.time() + 30
    while time.time() < deadline and os.path.isdir(
            update.staging_dir(installed)):
        time.sleep(0.2)

    check("the executable is the new one",
          read(installed, "DATUM.exe") == "binary v0.2.1",
          read(installed, "DATUM.exe"))
    check("the changed module is the new one",
          read(installed, "_internal/datum/core/hlr.py")
          == "hlr v2, now with hatching")
    check("the new module arrived",
          read(installed, "_internal/datum/core/bom.py") == "bom, brand new")
    check("the dropped module is gone",
          read(installed, "_internal/datum/ui/theme.py") is None)
    check("Qt was never touched",
          read(installed, "_internal/Qt6Core.dll")
          == "qt, very large, rarely changes")
    check("VTK was never touched",
          read(installed, "_internal/vtk.libs/vtkCommonCore.dll")
          == "vtk, never changes")
    check("the template survived",
          read(installed, "_internal/datum/templates/ISO A3.ddat")
          == "a template")

    print("and afterwards the installation is byte for byte the new build")
    after = update.manifest_of(installed)["files"]
    wanted = update.manifest_of(build_1)["files"]
    check("the same set of files", sorted(after) == sorted(wanted),
          set(after) ^ set(wanted))
    check("with the same hashes",
          all(after[k]["sha256"] == wanted[k]["sha256"] for k in wanted))
    check("so a second update has nothing to do",
          update.plan_for(manifest, installed).empty)
else:
    print("  SKIP  the swap is Windows-only")


# ==========================================================================
print("a signed release is checked the whole way through")

from cryptography.hazmat.primitives import serialization           # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (    # noqa: E402
    Ed25519PrivateKey)

private = Ed25519PrivateKey.generate()
public_hex = private.public_key().public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw).hex()

for name in (update.FEED_FILE, os.path.join("0.2.1", "manifest.json")):
    path = os.path.join(server, name)
    with open(path, "rb") as handle:
        signature = private.sign(handle.read()).hex()
    with open(path + ".sig", "w", encoding="ascii") as handle:
        handle.write(signature)

real_key = update.PUBLIC_KEY_HEX
update.PUBLIC_KEY_HEX = public_hex
real_fetch = update.fetch
update.fetch = lambda url, timeout=30.0: serve(url)
try:
    signed = update.check(BASE)
    check("a correctly signed feed is accepted", signed.version == "0.2.1",
          signed.version)
    body = update.fetch_signed(BASE, signed.manifest)
    check("and so is its manifest", b'"version": "0.2.1"' in body)

    print("but a tampered one is refused, and nothing is downloaded")
    feed_path = os.path.join(server, update.FEED_FILE)
    with open(feed_path, encoding="utf-8") as handle:
        good = handle.read()
    with open(feed_path, "w", encoding="utf-8") as handle:
        handle.write(good.replace('"0.2.1"', '"9.9.9"'))
    try:
        update.check(BASE)
        check("it refuses", False, "a tampered feed was accepted")
    except update.UpdateError as exc:
        check("it refuses", "not signed by IITEG" in str(exc), exc)
        check("and says nothing was changed", "Nothing has been changed"
              in str(exc), exc)
    with open(feed_path, "w", encoding="utf-8") as handle:
        handle.write(good)

    print("a release with no signature at all is refused too")
    os.remove(os.path.join(server, update.FEED_FILE + ".sig"))
    try:
        update.check(BASE)
        check("it refuses", False, "an unsigned feed was accepted")
    except update.UpdateError:
        check("it refuses", True)
finally:
    update.PUBLIC_KEY_HEX = real_key
    update.fetch = real_fetch


# ==========================================================================
print("release.py is runnable and says what it wants")

result = subprocess.run(
    [sys.executable, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tools", "release.py"), "--help"],
    capture_output=True, timeout=120)
check("it has a help page", result.returncode == 0,
      result.stderr.decode("utf-8", "replace")[-400:])
helptext = result.stdout.decode("utf-8", "replace")
for flag in ("--notes", "--make-key", "--minimum", "--skip-tests"):
    check("it offers %s" % flag, flag in helptext)

print("and it refuses to go backwards")
result = subprocess.run(
    [sys.executable, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tools", "release.py"), "0.0.1", "--skip-tests", "--no-tag"],
    capture_output=True, timeout=120)
check("a lower version is refused", result.returncode != 0)
check("and it says why", "not newer"
      in result.stderr.decode("utf-8", "replace"),
      result.stderr.decode("utf-8", "replace")[-300:])


# ==========================================================================
print()
if FAILS:
    print("%d FAILED" % len(FAILS))
    for name in FAILS:
        print("   - %s" % name)
    sys.exit(1)
print("all release checks passed")
