"""Cut a DATUM release: build it, hash it, sign it, lay it out for upload.

One command does the whole thing, because a release assembled by hand is
a release where the manifest and the files it describes eventually stop
agreeing, and the failure shows up on somebody else's machine.

    python tools/release.py 0.2.1 --notes "Hatched sections."

That bumps the version, runs the tests, builds, writes the manifest and
the feed, signs both, and leaves everything under release/ ready to be
copied to the server.  Nothing is uploaded and nothing is tagged until
the build has actually succeeded.

The private signing key is read from DATUM_SIGNING_KEY (a path) or from
release/signing-key.pem, and never goes near the repository.  Make one
with:

    python tools/release.py --make-key
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from datum.core import update                                  # noqa: E402

RELEASE = os.path.join(ROOT, "release")
DIST = os.path.join(ROOT, "dist", "DATUM")
INIT = os.path.join(ROOT, "datum", "__init__.py")
# Deliberately NOT inside release/.  That folder is what gets copied to
# the server, and a private signing key that goes up with it lets anybody
# sign an update that every installed copy will accept and install without
# asking.  It lives with the user's own files instead, and the old place
# is still read so an existing key keeps working - loudly.
from datum.core import prefs                                   # noqa: E402

KEY = prefs.config_path("signing-key.pem")
LEGACY_KEY = os.path.join(RELEASE, "signing-key.pem")
INNO = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs",
                    "Inno Setup 6", "ISCC.exe")


def say(text: str) -> None:
    print("  %s" % text, flush=True)


def fail(text: str) -> "NoReturn":       # noqa: F821
    print("\nSTOPPED: %s" % text, file=sys.stderr)
    sys.exit(1)


# ------------------------------------------------------------------ version


def current_version() -> str:
    with open(INIT, encoding="utf-8") as handle:
        found = re.search(r'__version__\s*=\s*"([^"]+)"', handle.read())
    return found.group(1) if found else "0.0.0"


def set_version(version: str) -> None:
    with open(INIT, encoding="utf-8", newline="") as handle:
        text = handle.read()
    text = re.sub(r'(__version__\s*=\s*")[^"]+(")',
                  r"\g<1>%s\g<2>" % version, text, count=1)
    with open(INIT, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def write_version_info(version: str) -> str:
    """The VERSIONINFO resource Windows shows in the file properties."""
    parts = list(update.parse_version(version)) + [0, 0, 0, 0]
    quad = ", ".join(str(p) for p in parts[:4])
    folder = os.path.join(ROOT, "build")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "version-info.txt")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(VERSION_INFO % {"quad": quad, "version": version})
    return path


VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(filevers=(%(quad)s), prodvers=(%(quad)s),
                    mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
        StringStruct('CompanyName', 'IITEG'),
        StringStruct('FileDescription', 'DATUM'),
        StringStruct('FileVersion', '%(version)s'),
        StringStruct('InternalName', 'DATUM'),
        StringStruct('LegalCopyright', 'SIA IITEG'),
        StringStruct('OriginalFilename', 'DATUM.exe'),
        StringStruct('ProductName', 'DATUM'),
        StringStruct('ProductVersion', '%(version)s')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


# ------------------------------------------------------------------ signing


def make_key() -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey)

    os.makedirs(os.path.dirname(KEY), exist_ok=True)
    if os.path.exists(KEY):
        fail("%s already exists.  Rotating the key means every installed "
             "copy stops accepting updates until it is reinstalled, so "
             "this will not overwrite it." % KEY)

    private = Ed25519PrivateKey.generate()
    with open(KEY, "wb") as handle:
        handle.write(private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()))
    public_hex = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw).hex()

    print("\nPrivate key written to %s" % KEY)
    print("It is gitignored.  Back it up somewhere safe and offline: lose")
    print("it and you cannot publish another update anybody will accept.\n")
    print("Now put the public half into datum/core/update.py:\n")
    print('    PUBLIC_KEY_HEX = "%s"\n' % public_hex)


def load_key():
    from cryptography.hazmat.primitives import serialization

    path = os.environ.get("DATUM_SIGNING_KEY") or KEY
    if not os.path.exists(path) and os.path.exists(LEGACY_KEY):
        path = LEGACY_KEY
        say("WARNING: the signing key is inside release/, which is the "
            "folder you upload.")
        say("         Move it to %s" % KEY)
        say("         and make sure no copy of it ever reaches the server.")
    if not os.path.exists(path):
        return None
    with open(path, "rb") as handle:
        return serialization.load_pem_private_key(handle.read(), password=None)


def sign_file(path: str, key) -> None:
    if key is None:
        return
    with open(path, "rb") as handle:
        signature = key.sign(handle.read()).hex()
    with open(path + ".sig", "w", encoding="ascii") as handle:
        handle.write(signature)


# ------------------------------------------------------------------- stages


def run_tests() -> None:
    say("running the test suite")
    result = subprocess.run([sys.executable, os.path.join("tests", "run.py")],
                            cwd=ROOT)
    if result.returncode != 0:
        fail("the tests did not pass, so nothing was released")


def build(version: str) -> None:
    say("building (this takes a few minutes)")
    write_version_info(version)
    shutil.rmtree(DIST, ignore_errors=True)
    environment = dict(os.environ, DATUM_VERSION=version)
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm",
         "--distpath", os.path.join(ROOT, "dist"),
         "--workpath", os.path.join(ROOT, "build", "work"),
         os.path.join("tools", "datum.spec")],
        cwd=ROOT, env=environment)
    if result.returncode != 0:
        fail("the build failed")
    if not os.path.exists(os.path.join(DIST, "DATUM.exe")):
        fail("the build produced no DATUM.exe")


def smoke_test() -> None:
    """Start the built application and make sure it does not fall over.

    A build can succeed and still be missing a DLL that only matters at
    import time, which is exactly the shape of the VTK problem.  Better to
    find that here than to publish it.
    """
    say("checking the build actually starts")
    exe = os.path.join(DIST, "DATUM.exe")
    report = os.path.join(ROOT, "build", "selftest.txt")
    if os.path.exists(report):
        os.remove(report)
    try:
        result = subprocess.run([exe, "--selftest", report], timeout=300)
        code = result.returncode
    except subprocess.TimeoutExpired:
        code = -1

    # A windowed build has no console, so the answer comes back in the
    # file rather than on stdout.
    detail = ""
    if os.path.exists(report):
        with open(report, encoding="utf-8", errors="replace") as handle:
            detail = handle.read()
    if code != 0 or "PASS" not in detail:
        fail("the built application failed its self test:\n\n%s"
             % (detail or "it produced no report at all, which usually "
                          "means it could not start: check that the VTK "
                          "DLLs were bundled."))
    for line in detail.strip().splitlines():
        say("  " + line)


def installer(version: str) -> str:
    """Build the Inno Setup installer, if Inno Setup is installed."""
    if not os.path.exists(INNO):
        say("Inno Setup not found, skipping the installer")
        return ""
    say("building the installer")
    # Inno's compiler takes / as its switch character, so every path handed
    # to it has to use backslashes or it reads the path as more switches
    # and complains about being given two scripts.
    out = os.path.join(RELEASE, version).replace("/", os.sep)
    script = os.path.join(ROOT, "tools", "installer.iss").replace("/", os.sep)
    result = subprocess.run(
        [INNO, "/DMyAppVersion=" + version, "/O" + out, script],
        cwd=ROOT, capture_output=True)
    if result.returncode != 0:
        fail("the installer failed:\n%s"
             % result.stdout.decode("utf-8", "replace")[-3000:])
    name = "DATUM-%s-setup.exe" % version
    return name if os.path.exists(
        os.path.join(RELEASE, version, name)) else ""


def publish(version: str, notes: str, minimum: str, key) -> str:
    """Lay out release/<version>/ and the feed beside it."""
    say("hashing %s" % DIST)
    folder = os.path.join(RELEASE, version)
    os.makedirs(folder, exist_ok=True)

    manifest = update.manifest_of(DIST)
    manifest["version"] = version
    manifest["built"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    say("copying %d file(s)" % len(manifest["files"]))
    for relative in manifest["files"]:
        source = os.path.join(DIST, relative.replace("/", os.sep))
        target = os.path.join(folder, relative.replace("/", os.sep))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(source, target)

    manifest_path = os.path.join(folder, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=1, sort_keys=True)
    sign_file(manifest_path, key)

    total = sum(e["size"] for e in manifest["files"].values())
    setup = installer(version)

    feed = {
        "version": version,
        "released": time.strftime("%Y-%m-%d"),
        "notes": notes,
        "manifest": "%s/manifest.json" % version,
        "minimum": minimum,
        "size": total,
    }
    if setup:
        feed["installer"] = "%s/%s" % (version, setup)

    feed_path = os.path.join(RELEASE, update.FEED_FILE)
    with open(feed_path, "w", encoding="utf-8") as handle:
        json.dump(feed, handle, indent=1)
    sign_file(feed_path, key)
    return folder


# --------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", nargs="?",
                        help="the version to release, e.g. 0.2.1")
    parser.add_argument("--notes", default="",
                        help="what changed, shown in the update banner")
    parser.add_argument("--minimum", default="0.0.0",
                        help="oldest version that can patch forward to this")
    parser.add_argument("--make-key", action="store_true",
                        help="create the release signing key and stop")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--no-tag", action="store_true")
    args = parser.parse_args()

    if args.make_key:
        make_key()
        return 0
    if not args.version:
        parser.error("a version is required, e.g. 0.2.1")

    version = args.version.lstrip("v")
    if not re.match(r"^\d+\.\d+\.\d+", version):
        fail("%r does not look like a version" % version)
    if update.parse_version(version) <= update.parse_version(
            current_version()) and version != current_version():
        fail("%s is not newer than the current %s"
             % (version, current_version()))

    key = load_key()
    if key is None:
        say("no signing key, so this release will be unsigned")
        if update.signing_enabled():
            fail("but this build checks signatures, so an unsigned release "
                 "would be refused by every installed copy.  Set "
                 "DATUM_SIGNING_KEY or run --make-key.")

    print("\nDATUM %s  (from %s)\n" % (version, current_version()))
    if version != current_version():
        say("setting the version")
        set_version(version)

    if not args.skip_tests:
        run_tests()
    build(version)
    smoke_test()
    folder = publish(version, args.notes, args.minimum, key)

    if not args.no_tag:
        say("tagging")
        subprocess.run(["git", "add", "-A"], cwd=ROOT)
        subprocess.run(["git", "commit", "-m", "DATUM %s" % version],
                       cwd=ROOT)
        subprocess.run(["git", "tag", "-a", "v%s" % version,
                        "-m", args.notes or ("DATUM %s" % version)], cwd=ROOT)

    print("""
Done.  %s

Upload to https://api.iiteg.com/datum/, keeping the layout:

    release/%s/       ->  /datum/%s/        (all of it, 545 files)
    release/latest.json      ->  /datum/latest.json
    release/latest.json.sig  ->  /datum/latest.json.sig

Send the version folder FIRST and the feed last.  A copy that reads the
feed before the files are there will try to update and fail.

Upload nothing else out of release/.  Not signing-key.pem, which is the
one thing that decides whether an update is really yours, and not the
.zip archives, which are only there to keep a copy of an old build.

Installed copies pick it up within six hours, or at once from
File then Check for Updates.

Only the newest version folder is ever fetched from, whatever version
somebody is updating from, so older ones can be deleted from the server
and from release/ once a release has settled.  Keep one behind if you
want somewhere to roll back to.""" % (folder, version, version))
    return 0


if __name__ == "__main__":
    sys.exit(main())
