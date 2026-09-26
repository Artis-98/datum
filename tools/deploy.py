"""Put a release on the server, and check what is actually there.

Two uploads went wrong today in the same way: the feed arrived, the files
did not, and nothing said so. A release is not published when the files
leave this machine, it is published when they are all on the server and
the feed points at them - so this does the upload and the checking as one
job, in the order that cannot break.

    python tools/deploy.py check                what is published now
    python tools/deploy.py release 0.2.1        upload it, feed last
    python tools/deploy.py site                 upload the website
    python tools/deploy.py prune --keep 1       drop old version folders

``check`` needs no credentials at all: it reads the public URLs the way an
installed copy does, verifies the signatures, and works out whether an
update would actually go through. Run it after any upload.

Why FTP: the host has no SSH, and Pure-FTPd there offers AUTH TLS, so the
password and the files go encrypted. SIZE lets a re-run skip what is
already up, which turns a failed upload into something you can simply run
again rather than start over.

Credentials live in deploy.json beside the preferences, outside this
repository and never in it. The path is printed by any command that needs
it. It looks like this:

    {
      "api":  {"host": "api.iiteg.com",   "user": "datum@iiteg.com",
               "password": "...", "root": "/datum"},
      "site": {"host": "datum.iiteg.com", "user": "...",
               "password": "...", "root": "/"}
    }

Make that FTP account in cPanel restricted to the folder it needs, not the
main login: if it ever leaks, the damage is one directory. The password
can be left out of the file and given as DATUM_DEPLOY_PASSWORD instead.
"""

from __future__ import annotations

import argparse
import ftplib
import json
import os
import ssl
import sys
import time
from typing import Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from datum.core import prefs, update                           # noqa: E402

RELEASE = os.path.join(ROOT, "release")
SITE = os.path.join(ROOT, "site")
CONFIG = prefs.config_path("deploy.json")


def say(text: str) -> None:
    print("  %s" % text, flush=True)


def fail(text: str) -> "NoReturn":                             # noqa: F821
    print("\nSTOPPED: %s" % text, file=sys.stderr)
    sys.exit(1)


# ------------------------------------------------------------------ the link


def settings(target: str) -> Dict[str, str]:
    if not os.path.exists(CONFIG):
        fail("no %s\n         See the top of tools/deploy.py for what goes "
             "in it." % CONFIG)
    with open(CONFIG, encoding="utf-8") as handle:
        data = json.load(handle)
    if target not in data:
        fail("%s has no %r section" % (CONFIG, target))
    where = dict(data[target])
    if not where.get("password"):
        where["password"] = os.environ.get("DATUM_DEPLOY_PASSWORD", "")
    for key in ("host", "user", "password"):
        if not where.get(key):
            fail("the %r section is missing %r" % (target, key))
    return where


class Link:
    """One FTPS connection, with the few operations a deploy needs."""

    def __init__(self, where: Dict[str, str]) -> None:
        self.root = str(where.get("root", "/")).rstrip("/")
        self.ftp = ftplib.FTP_TLS(context=ssl.create_default_context())
        self.ftp.connect(where["host"], int(where.get("port", 21)), timeout=30)
        self.ftp.login(where["user"], where["password"])
        # encrypt the data channel too, not only the login
        self.ftp.prot_p()
        self.ftp.set_pasv(True)

    def close(self) -> None:
        try:
            self.ftp.quit()
        except Exception:
            try:
                self.ftp.close()
            except Exception:
                pass

    def path(self, relative: str) -> str:
        if not relative:
            return self.root or "/"
        return "%s/%s" % (self.root, relative.lstrip("/"))

    def size(self, relative: str) -> Optional[int]:
        try:
            return self.ftp.size(self.path(relative))
        except (ftplib.error_perm, ftplib.error_temp, OSError):
            return None

    def ensure_dir(self, relative: str) -> None:
        """Make a directory and everything above it, quietly."""
        here = self.root
        for part in [p for p in relative.split("/") if p]:
            here += "/" + part
            try:
                self.ftp.mkd(here)
            except ftplib.error_perm:
                pass            # already there, which is the usual case

    def put(self, local: str, relative: str) -> None:
        folder = os.path.dirname(relative)
        if folder:
            self.ensure_dir(folder)
        with open(local, "rb") as handle:
            self.ftp.storbinary("STOR " + self.path(relative), handle,
                                blocksize=1 << 16)

    def delete(self, relative: str) -> bool:
        try:
            self.ftp.delete(self.path(relative))
            return True
        except ftplib.error_perm:
            return False

    def listing(self, relative: str = "") -> List[Tuple[str, str]]:
        """(name, type) for one directory, using MLSD where it is offered."""
        out: List[Tuple[str, str]] = []
        try:
            for name, facts in self.ftp.mlsd(self.path(relative)):
                if name not in (".", ".."):
                    out.append((name, facts.get("type", "file")))
            return out
        except Exception:
            pass
        try:
            for name in self.ftp.nlst(self.path(relative)):
                base = name.rsplit("/", 1)[-1]
                if base not in (".", ".."):
                    out.append((base, "file"))
        except Exception:
            pass
        return out

    def remove_tree(self, relative: str) -> int:
        """Delete a folder and everything in it.  Returns the file count."""
        gone = 0
        for name, kind in self.listing(relative):
            child = "%s/%s" % (relative.rstrip("/"), name)
            if kind == "dir":
                gone += self.remove_tree(child)
            elif self.delete(child):
                gone += 1
        try:
            self.ftp.rmd(self.path(relative))
        except ftplib.error_perm:
            pass
        return gone


# --------------------------------------------------------------- uploading


def upload_set(link: Link, pairs: List[Tuple[str, str]],
               force: bool = False) -> Tuple[int, int, int]:
    """Send local -> remote pairs, skipping any that are already right.

    Skipping by size rather than by hash, because FTP cannot hash and
    because of what this is for: a transfer that stopped part way. Every
    file it did send is the right size and every one it did not is
    absent, so size is enough to tell them apart. ``--force`` re-sends
    everything regardless.
    """
    sent = skipped = failed = 0
    moved = 0
    total = len(pairs)
    for index, (local, relative) in enumerate(pairs, 1):
        want = os.path.getsize(local)
        if not force and link.size(relative) == want:
            skipped += 1
        else:
            try:
                link.put(local, relative)
                sent += 1
                moved += want
            except Exception as exc:
                failed += 1
                say("FAILED %s: %s" % (relative, exc))
        if index % 25 == 0 or index == total:
            say("%4d/%d  sent %d, already there %d, failed %d  (%s)"
                % (index, total, sent, skipped, failed, update.human(moved)))
    return sent, skipped, failed


def release_pairs(version: str) -> List[Tuple[str, str]]:
    folder = os.path.join(RELEASE, version)
    if not os.path.isdir(folder):
        fail("no %s" % folder)
    manifest_path = os.path.join(folder, "manifest.json")
    if not os.path.exists(manifest_path):
        fail("no manifest in %s - was this cut with tools/release.py?"
             % folder)
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)

    pairs: List[Tuple[str, str]] = []
    for relative in sorted(manifest["files"]):
        local = os.path.join(folder, relative.replace("/", os.sep))
        if not os.path.exists(local):
            fail("%s is in the manifest but not on disk" % relative)
        pairs.append((local, "%s/%s" % (version, relative)))
    # the installer is not in the manifest, but the website links to it
    for extra in sorted(os.listdir(folder)):
        if extra.endswith("-setup.exe"):
            pairs.append((os.path.join(folder, extra),
                          "%s/%s" % (version, extra)))
    return pairs


def do_release(version: str, force: bool = False) -> int:
    """Upload a release in the one order that cannot half-break.

    Files first, then the manifest that describes them, then the feed that
    points at the manifest. A copy that checks for updates while this is
    running sees the version it already has and does nothing, because the
    feed is the last thing to change.
    """
    pairs = release_pairs(version)
    say("%d file(s) to consider for %s" % (len(pairs), version))

    where = settings("api")
    link = Link(where)
    try:
        say("connected to %s as %s, TLS on" % (where["host"], where["user"]))
        sent, skipped, failed = upload_set(link, pairs, force)
        say("payload done: %d sent, %d already there, %d failed"
            % (sent, skipped, failed))
        if failed:
            fail("%d file(s) did not go up. Run this again - it will only "
                 "send what is still missing." % failed)

        folder = os.path.join(RELEASE, version)
        for name in ("manifest.json", "manifest.json.sig"):
            local = os.path.join(folder, name)
            if os.path.exists(local):
                link.put(local, "%s/%s" % (version, name))
                say("sent %s/%s" % (version, name))

        for name in (update.FEED_FILE, update.FEED_FILE + ".sig"):
            local = os.path.join(RELEASE, name)
            if os.path.exists(local):
                link.put(local, name)
                say("sent %s  (last, on purpose)" % name)
    finally:
        link.close()
    print()
    return do_check(version)


def do_site() -> int:
    """Upload the website: every file under site/, bar the zip."""
    pairs = []
    for base, _dirs, files in os.walk(SITE):
        for name in sorted(files):
            if name.endswith(".zip"):
                continue
            local = os.path.join(base, name)
            relative = os.path.relpath(local, SITE).replace(os.sep, "/")
            pairs.append((local, relative))
    say("%d file(s) to consider" % len(pairs))

    where = settings("site")
    link = Link(where)
    failed = 0
    try:
        say("connected to %s as %s, TLS on" % (where["host"], where["user"]))
        sent, skipped, failed = upload_set(link, pairs)
        say("done: %d sent, %d already there, %d failed"
            % (sent, skipped, failed))
    finally:
        link.close()
    return 1 if failed else 0


def do_prune(keep: int) -> int:
    """Delete old version folders, keeping the newest few."""
    where = settings("api")
    link = Link(where)
    try:
        versions = []
        for name, kind in link.listing(""):
            if kind != "dir":
                continue
            try:
                versions.append((update.parse_version(name), name))
            except Exception:
                continue
        versions.sort(reverse=True)
        if len(versions) <= keep:
            say("%d version folder(s) there, keeping all of them"
                % len(versions))
            return 0
        for _parsed, name in versions[keep:]:
            say("removing %s" % name)
            say("  %d file(s) deleted" % link.remove_tree(name))
    finally:
        link.close()
    return 0


# ---------------------------------------------------------------- checking


def do_check(expect: str = "") -> int:
    """Read the published channel the way an installed copy reads it."""
    import concurrent.futures as cf
    import urllib.parse
    import urllib.request

    problems = []
    print("the feed")
    try:
        release = update.check()
    except Exception as exc:
        print("  UNREADABLE: %s" % exc)
        return 1
    print("  version %s, released %s, signature verified"
          % (release.version, release.released))
    if expect and release.version != expect:
        problems.append("the feed says %s, not %s" % (release.version, expect))

    print()
    print("the manifest")
    try:
        served = update.fetch_signed(release.base, release.manifest)
    except Exception as exc:
        print("  MISSING: %s" % exc)
        return 1
    manifest = json.loads(served.decode("utf-8"))
    print("  present, signature verified, %d file(s) listed"
          % len(manifest["files"]))
    local_manifest = os.path.join(RELEASE, release.version, "manifest.json")
    if os.path.exists(local_manifest):
        with open(local_manifest, "rb") as handle:
            same = handle.read() == served
        print("  identical to release/%s/manifest.json: %s"
              % (release.version, same))
        if not same:
            problems.append("the served manifest is not the one we built")

    print()
    print("the files")
    base = release.base + release.version + "/"

    def probe(item):
        name, entry = item
        # quote the name, the way the updater does: five of the drawing
        # templates have spaces in them, and an unquoted HEAD for those
        # comes back 404 from a server that is holding them perfectly well
        request = urllib.request.Request(
            base + urllib.parse.quote(name), method="HEAD",
            headers={"User-Agent": "DATUM-deploy-check"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                got = int(response.headers.get("Content-Length", -1))
                return name, entry, got == entry["size"]
        except Exception:
            return name, entry, False

    with cf.ThreadPoolExecutor(max_workers=12) as pool:
        rows = list(pool.map(probe, manifest["files"].items()))
    bad = [row for row in rows if not row[2]]
    print("  %d of %d present and the right size"
          % (len(rows) - len(bad), len(rows)))
    if bad:
        print("  missing %d file(s), %s"
              % (len(bad),
                 update.human(sum(e["size"] for _n, e, _ok in bad))))
        for name, _entry, _ok in bad[:5]:
            print("    %s" % name)
        problems.append("%d file(s) are not on the server" % len(bad))

    if release.installer:
        status = "missing"
        try:
            request = urllib.request.Request(
                release.base + release.installer, method="HEAD",
                headers={"User-Agent": "DATUM-deploy-check"})
            with urllib.request.urlopen(request, timeout=30) as response:
                status = "%s bytes" % response.headers.get("Content-Length")
        except Exception:
            problems.append("the installer the website links to is missing")
        print("  installer: %s" % status)

    print()
    if problems:
        for problem in problems:
            print("  PROBLEM: %s" % problem)
        print("\nNot published properly yet.")
        return 1
    print("Published: the feed, the manifest and every file agree.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="what", required=True)

    one = sub.add_parser("release", help="upload a release, feed last")
    one.add_argument("version")
    one.add_argument("--force", action="store_true",
                     help="re-send every file, not only the missing ones")

    sub.add_parser("site", help="upload the website")

    two = sub.add_parser("check", help="verify what is published")
    two.add_argument("version", nargs="?", default="")

    three = sub.add_parser("prune", help="delete old version folders")
    three.add_argument("--keep", type=int, default=1)

    args = parser.parse_args()
    started = time.time()
    if args.what == "release":
        code = do_release(args.version, args.force)
    elif args.what == "site":
        code = do_site()
    elif args.what == "prune":
        code = do_prune(args.keep)
    else:
        code = do_check(args.version)
    print("\n(%.0f s)" % (time.time() - started))
    return code


if __name__ == "__main__":
    sys.exit(main())
