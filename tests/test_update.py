"""The update channel: what it fetches, what it refuses, and the swap."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datum import __version__                                  # noqa: E402
from datum.core import swap, update                            # noqa: E402

FAILS = []
WORK = tempfile.mkdtemp(prefix="datum_update_")


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
    return path


# ==========================================================================
print("versions compare as numbers, not as text")

check("0.2.10 is newer than 0.2.9",
      update.is_newer("0.2.10", "0.2.9"))
check("and 0.10.0 is newer than 0.9.9",
      update.is_newer("0.10.0", "0.9.9"))
check("the same version is not newer",
      not update.is_newer("0.2.0", "0.2.0"))
check("nor is an older one", not update.is_newer("0.1.9", "0.2.0"))
check("a short version is padded, not misread",
      update.parse_version("1.2") == (1, 2, 0),
      update.parse_version("1.2"))
check("a suffix does not throw, it just stops the parse",
      update.parse_version("0.3.0-beta2") == (0, 3, 0),
      update.parse_version("0.3.0-beta2"))
check("and nonsense reads as nothing rather than crashing",
      update.parse_version("") == (0, 0, 0))


# ==========================================================================
print("a source checkout refuses to update itself")

ok, why = update.can_update()
check("it says no", not ok)
check("and says why", "source" in why, why)
check("because nothing here is frozen", not update.frozen())


# ==========================================================================
print("a manifest is every file, hashed")

old = os.path.join(WORK, "install")
write(old, "DATUM.exe", "pretend binary v1")
write(old, "_internal/datum/core/hlr.py", "print('hlr v1')")
write(old, "_internal/datum/ui/theme.py", "print('theme v1')")
write(old, "_internal/Qt6Core.dll", "a large unchanging thing")

manifest = update.manifest_of(old)
check("it found every file", len(manifest["files"]) == 4,
      sorted(manifest["files"]))
check("paths are relative with forward slashes",
      "_internal/datum/core/hlr.py" in manifest["files"],
      sorted(manifest["files"]))
check("each one has a hash",
      all(len(e["sha256"]) == 64 for e in manifest["files"].values()))
check("and a size",
      all(e["size"] > 0 for e in manifest["files"].values()))

print("hashing the same bytes twice gives the same answer")
check("stable", update.file_hash(os.path.join(old, "DATUM.exe"))
      == update.file_hash(os.path.join(old, "DATUM.exe")))
check("a file that is not there hashes to nothing, it does not throw",
      update.file_hash(os.path.join(old, "nope.dll")) == "")


# ==========================================================================
print("a plan downloads only what changed")

new = os.path.join(WORK, "build")
write(new, "DATUM.exe", "pretend binary v2")                # changed
write(new, "_internal/datum/core/hlr.py", "print('hlr v2')")  # changed
write(new, "_internal/datum/ui/theme.py", "print('theme v1')")  # same
write(new, "_internal/Qt6Core.dll", "a large unchanging thing")  # same
write(new, "_internal/datum/core/bom.py", "print('new file')")  # added
# _internal/datum/ui/theme.py stays, nothing is dropped yet

new_manifest = update.manifest_of(new)
new_manifest["version"] = "0.2.1"
plan = update.plan_for(new_manifest, old)

check("the two unchanged files are left alone", plan.unchanged == 2,
      plan.unchanged)
check("the changed and added ones are fetched",
      sorted(plan.download) == ["DATUM.exe",
                                "_internal/datum/core/bom.py",
                                "_internal/datum/core/hlr.py"],
      sorted(plan.download))
check("Qt is not in the download", "_internal/Qt6Core.dll"
      not in plan.download)
check("nothing is deleted, because nothing was dropped", not plan.delete,
      plan.delete)
check("it knows how much it has to fetch", plan.bytes_needed > 0,
      plan.bytes_needed)
check("and it is not empty", not plan.empty)

print("a file the release dropped is deleted, not left behind")
shrunk = update.manifest_of(new)
del shrunk["files"]["_internal/datum/ui/theme.py"]
gone = update.plan_for(shrunk, old)
check("it is listed for deletion",
      gone.delete == ["_internal/datum/ui/theme.py"], gone.delete)

print("the uninstaller is never deleted, however old the manifest is")
write(old, "unins000.exe", "the uninstaller Inno left behind")
write(old, "unins000.dat", "its log")
kept = update.plan_for(shrunk, old)
check("neither is marked for deletion",
      not any("unins" in p for p in kept.delete), kept.delete)
check("but a genuinely dropped file still is",
      kept.delete == ["_internal/datum/ui/theme.py"], kept.delete)
os.remove(os.path.join(old, "unins000.exe"))
os.remove(os.path.join(old, "unins000.dat"))

print("an install that is already current has nothing to do")
same = update.plan_for(update.manifest_of(old), old)
check("no downloads", not same.download, same.download)
check("no deletions", not same.delete, same.delete)
check("and it says so", same.empty)
check("in words", same.summary() == "everything is already up to date",
      same.summary())


# ==========================================================================
print("a manifest cannot reach outside the installation")

for hostile in ("../evil.dll", "..\\evil.dll", "C:/Windows/evil.dll",
                "/etc/passwd", "a/../../b.dll"):
    try:
        update.plan_for({"files": {hostile: {"size": 1, "sha256": "x"}}}, old)
        check("%r is refused" % hostile, False, "it was accepted")
    except update.UpdateError:
        check("%r is refused" % hostile, True)

print("an empty manifest is refused rather than deleting everything")
try:
    update.plan_for({"files": {}}, old)
    check("it throws", False, "an empty manifest was accepted")
except update.UpdateError:
    check("it throws", True)


# ==========================================================================
print("downloading checks every file before anything is applied")

served = {}
for relative in update.manifest_of(new)["files"]:
    with open(os.path.join(new, relative.replace("/", os.sep)), "rb") as f:
        served["https://example.invalid/0.2.1/" + relative] = f.read()

release = update.Release(version="0.2.1", manifest="0.2.1/manifest.json",
                         base="https://example.invalid/")


def serve(url):
    if url not in served:
        raise update.UpdateError("404 %s" % url)
    return served[url]


plan = update.plan_for(new_manifest, old, "0.2.1")
staging = update.download(plan, release, fetcher=serve)
check("a staging folder appeared", os.path.isdir(staging))
check("it holds exactly what was asked for",
      sorted(p for p in update.manifest_of(staging)["files"]
             if p != update.PLAN_FILE) == sorted(plan.download),
      sorted(update.manifest_of(staging)["files"]))
check("the installation has not been touched yet",
      open(os.path.join(old, "DATUM.exe"), encoding="utf-8").read()
      == "pretend binary v1")
check("and the plan was written down", update.pending(old) == "0.2.1",
      update.pending(old))

print("a file that arrives corrupted takes the whole update down with it")
bad = dict(served)
bad["https://example.invalid/0.2.1/DATUM.exe"] = b"tampered"
try:
    update.download(update.plan_for(new_manifest, old, "0.2.1"), release,
                    fetcher=lambda u: bad.get(u) or serve(u))
    check("it refuses", False, "a bad file was accepted")
except update.UpdateError as exc:
    check("it refuses", "did not arrive intact" in str(exc), exc)
check("and it cleaned up after itself, leaving no half update",
      update.pending(old) is None, update.pending(old))

print("a download that fails part way leaves nothing behind")
try:
    update.download(update.plan_for(new_manifest, old, "0.2.1"), release,
                    fetcher=lambda u: (_ for _ in ()).throw(
                        update.UpdateError("the network went away")))
    check("it raises", False)
except update.UpdateError:
    check("it raises", True)
check("with no staging left", not os.path.isdir(update.staging_dir(old)))


# ==========================================================================
print("plain http is refused outright")

try:
    update.fetch("http://api.iiteg.com/datum/latest.json")
    check("it refuses", False, "http was accepted")
except update.UpdateError as exc:
    check("it refuses", "refusing" in str(exc), exc)


# ==========================================================================
print("the feed is read into something usable")

feed = json.dumps({"version": "0.3.0", "released": "2026-10-01",
                   "notes": "Hatching on sections.",
                   "manifest": "0.3.0/manifest.json",
                   "minimum": "0.2.0"}).encode("utf-8")
found = update.check("https://example.invalid/datum/",
                     fetcher=lambda base, name: feed)
check("the version came through", found.version == "0.3.0", found.version)
check("it is newer than this build", found.newer)
check("the notes came through", "Hatching" in found.notes)
check("and it does not demand a reinstall", not found.needs_reinstall)

print("a jump too far to patch asks for the installer instead")
far = update.check("https://example.invalid/datum/",
                   fetcher=lambda b, n: json.dumps(
                       {"version": "2.0.0", "minimum": "1.5.0"}).encode())
check("it says so", far.needs_reinstall)

print("a feed that says nothing useful is an error, not a silent no-op")
for body in (b"{}", b"not json at all", b'{"version": ""}'):
    try:
        update.check("https://example.invalid/d/", fetcher=lambda b, n: body)
        check("%r is refused" % body[:20], False, "accepted")
    except update.UpdateError:
        check("%r is refused" % body[:20], True)


# ==========================================================================
print("signatures")

check("this build has a release key, so it checks what it downloads",
      update.signing_enabled(), update.PUBLIC_KEY_HEX)
check("the key is a real Ed25519 public key",
      len(update.PUBLIC_KEY_HEX) == 64
      and all(c in "0123456789abcdef" for c in update.PUBLIC_KEY_HEX),
      update.PUBLIC_KEY_HEX)
check("this build refuses an unsigned payload",
      not update.verify(b"anything", ""))
check("and an empty key argument cannot switch the check off, it falls "
      "back to the built-in one",
      not update.verify(b"anything", "", ""))

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey)
    from cryptography.hazmat.primitives import serialization

    private = Ed25519PrivateKey.generate()
    public_hex = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw).hex()
    payload = b'{"version": "0.3.0"}'
    signature = private.sign(payload).hex()

    check("a real signature verifies",
          update.verify(payload, signature, public_hex))
    check("a tampered payload does not",
          not update.verify(payload + b" ", signature, public_hex))
    check("nor does a signature from a different key",
          not update.verify(payload,
                            Ed25519PrivateKey.generate().sign(payload).hex(),
                            public_hex))
    check("nor does rubbish",
          not update.verify(payload, "not hex", public_hex))
except ImportError:
    print("  SKIP  cryptography is not installed")


# ==========================================================================
print("the swap script says what it is going to do")

script = swap.script_for(r"C:\App", r"C:\App\.staging", r"C:\App\DATUM.exe",
                         4321, ["_internal/old.pyd"])
check("it waits for the process to go", '"PID eq 4321"' in script, script[:200])
check("it deletes what the release dropped",
      r"del /f /q \"C:\App\_internal\old.pyd\"".replace("\\\"", '"')
      in script.replace('"', '"'),
      [ln for ln in script.splitlines() if "old.pyd" in ln])
check("it moves staging over the installation",
      "/MOVE" in script and r"C:\App\.staging" in script)
check("it checks robocopy really worked",
      "if errorlevel 8 goto failed" in script)
check("it starts DATUM again", r'start "" "C:\App\DATUM.exe"' in script)
check("and it deletes itself",
      'del "%~f0"' in script)
check("it is CRLF, the way a batch file has to be",
      "\r\n" in script and "\n" not in script.replace("\r\n", ""))

print("and it can be told not to relaunch")
quiet = swap.script_for(r"C:\App", r"C:\App\.staging", r"C:\App\DATUM.exe",
                        1, [], relaunch=False)
check("no start line", 'start ""' not in quiet)


# ==========================================================================
print("the swap really does swap, run for real on a fake installation")

if os.name == "nt":
    live = os.path.join(WORK, "live")
    shutil.rmtree(live, ignore_errors=True)
    write(live, "DATUM.exe", "v1")
    write(live, "_internal/keep.dll", "unchanged")
    write(live, "_internal/stale.pyd", "should be deleted")
    stage = update.staging_dir(live)
    write(stage, "DATUM.exe", "v2")
    write(stage, "_internal/new.py", "brand new")
    with open(os.path.join(stage, update.PLAN_FILE), "w",
              encoding="utf-8") as handle:
        json.dump({"version": "0.2.1", "download": [],
                   "delete": ["_internal/stale.pyd"]}, handle)

    # pid 1 is not a real Windows process, so the script proceeds at once
    text = swap.script_for(live, stage, "cmd.exe /c exit", 1,
                           ["_internal/stale.pyd"], relaunch=False)
    path = os.path.join(WORK, "doswap.cmd")
    with open(path, "w", encoding="ascii", newline="") as handle:
        handle.write(text)
    subprocess.run(["cmd.exe", "/c", path], capture_output=True, timeout=90)

    deadline = time.time() + 20
    while time.time() < deadline and os.path.isdir(stage):
        time.sleep(0.2)

    def read(rel):
        try:
            with open(os.path.join(live, rel.replace("/", os.sep)),
                      encoding="utf-8") as handle:
                return handle.read()
        except OSError:
            return None

    check("the changed file was replaced", read("DATUM.exe") == "v2",
          read("DATUM.exe"))
    check("the new file arrived", read("_internal/new.py") == "brand new",
          read("_internal/new.py"))
    check("the unchanged file was left alone",
          read("_internal/keep.dll") == "unchanged")
    check("the dropped file is gone", read("_internal/stale.pyd") is None,
          read("_internal/stale.pyd"))
    check("the staging folder was cleaned up", not os.path.isdir(stage),
          os.listdir(stage) if os.path.isdir(stage) else "")
    check("and nothing thinks an update is still pending",
          update.pending(live) is None, update.pending(live))
    check("the script deleted itself", not os.path.exists(path))
else:
    print("  SKIP  the swap is Windows-only")


# ==========================================================================
print()
if FAILS:
    print("%d FAILED" % len(FAILS))
    for name in FAILS:
        print("   - %s" % name)
    sys.exit(1)
print("all update checks passed")
