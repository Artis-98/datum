"""Every document open at once, and what each one knows about the others.

DATUM keeps all open files in a single process behind one tab strip, the way
Inventor does.  That makes two things possible that a window-per-document
application cannot do well:

    a part being edited in its own tab is the *same object* the assembly
    refers to, so there is no save-and-reload round trip;

    and because it is the same object, the assembly can be told that its
    part has moved on without being forced to rebuild there and then.

That second point is what Local Update is.  Each open document carries a
``revision`` that goes up whenever it is rebuilt, and a ``published``
revision, which is what the assemblies referring to it currently see.  They
drift apart as you edit; Local Update closes the gap.  Nothing updates behind
your back, which matters when a rebuild of a large assembly is not free.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..core import fileformat


def same_file(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(
        os.path.abspath(b))


@dataclass
class OpenDocument:
    """One document in a tab."""

    key: str = ""
    document: Any = None
    doc_type: str = fileformat.PART

    # bumped every time this document rebuilds, so anything referring to it
    # can tell that it has moved on
    revision: int = 0
    # the revision assemblies and sheets currently see, and the body they
    # see it as.  Only Local Update moves these forward.
    published: int = 0
    published_shape: Any = None

    # what this document was built against, when it refers to others:
    # absolute path -> the revision of that document at the time
    sources: Dict[str, int] = field(default_factory=dict)

    camera: Any = None
    untitled: int = 0
    # digest of the document's contents the last time it was looked at, so a
    # rebuild that changed nothing does not count as a change
    signature: Optional[str] = None

    @property
    def path(self) -> str:
        return getattr(self.document, "path", "") or ""

    @property
    def title(self) -> str:
        return getattr(self.document, "title", "Untitled")

    @property
    def modified(self) -> bool:
        return bool(getattr(self.document, "modified", False))

    @property
    def label(self) -> str:
        """What the tab says: a real file name, or the placeholder title."""
        if self.path:
            return os.path.basename(self.path)
        return "%s%s" % (self.title,
                         fileformat.extension_for(self.doc_type))

    @property
    def is_part(self) -> bool:
        return self.doc_type == fileformat.PART

    @property
    def references(self) -> List[str]:
        """Absolute paths of the files this document places, if any."""
        document = self.document
        base = getattr(document, "base_dir", "")
        out: List[str] = []
        for ref in getattr(document, "components", []) or []:
            resolved = ref.resolve(base) if base else (
                ref.path if ref.path and os.path.exists(ref.path) else None)
            if resolved:
                out.append(os.path.abspath(resolved))
        return out

    def publish(self) -> None:
        """Make this document's current body the one others will see."""
        self.published_shape = getattr(self.document, "shape", None)
        self.published = self.revision

    def digest(self) -> Optional[str]:
        try:
            return hashlib.blake2b(self.document.snapshot().encode("utf-8"),
                                   digest_size=16).hexdigest()
        except Exception:
            return None

    def note_change(self) -> bool:
        """Bump the revision only when the document's contents really moved.

        A rebuild is not a change: previewing a dialog, cancelling it, or
        simply switching to a tab all rebuild, and if any of those counted
        then every assembly would claim to be out of date the moment you
        looked at one of its parts.  Comparing a digest of the document
        makes the answer exact.
        """
        current = self.digest()
        if current is None:
            self.revision += 1
            return True
        if self.signature is None:
            self.signature = current
            return False
        if current == self.signature:
            return False
        self.signature = current
        self.revision += 1
        return True

    def touch(self) -> None:
        self.revision += 1
        self.signature = self.digest()


class Session:
    """The set of open documents, and the questions the window asks of it."""

    def __init__(self) -> None:
        self.documents: List[OpenDocument] = []
        self.active: Optional[OpenDocument] = None
        self._next_key = 1
        self._untitled: Dict[str, int] = {}

    # ------------------------------------------------------------- contents

    def __len__(self) -> int:
        return len(self.documents)

    def __iter__(self):
        return iter(self.documents)

    def add(self, document: Any, doc_type: str) -> OpenDocument:
        entry = OpenDocument(key="doc%d" % self._next_key, document=document,
                             doc_type=doc_type)
        self._next_key += 1
        if not getattr(document, "path", ""):
            count = self._untitled.get(doc_type, 0) + 1
            self._untitled[doc_type] = count
            entry.untitled = count
        self.documents.append(entry)
        return entry

    def remove(self, entry: OpenDocument) -> None:
        if entry in self.documents:
            self.documents.remove(entry)
        if self.active is entry:
            self.active = None

    def by_key(self, key: str) -> Optional[OpenDocument]:
        return next((e for e in self.documents if e.key == key), None)

    def by_path(self, path: str) -> Optional[OpenDocument]:
        return next((e for e in self.documents if same_file(e.path, path)),
                    None)

    def by_document(self, document: Any) -> Optional[OpenDocument]:
        return next((e for e in self.documents if e.document is document),
                    None)

    def parts(self) -> List[OpenDocument]:
        return [e for e in self.documents if e.is_part]

    def modified(self) -> List[OpenDocument]:
        return [e for e in self.documents if e.modified]

    # ------------------------------------------------------- live geometry

    def live_shape(self, path: str) -> Any:
        """The body an assembly should use for a file that is also open.

        Returns the *published* body, not the live one, so an edit next door
        does not appear until Local Update says so.  A document that has
        never published does so now, which is what makes opening an assembly
        and a part together behave sensibly from the first rebuild.
        """
        entry = self.by_path(path)
        if entry is None or not entry.is_part:
            return None
        if entry.published_shape is None:
            entry.publish()
        return entry.published_shape

    def publish_all(self) -> int:
        """Let every open part's current state through. Returns how many moved."""
        moved = 0
        for entry in self.parts():
            if entry.published != entry.revision or entry.published_shape is None:
                entry.publish()
                moved += 1
        return moved

    # ---------------------------------------------------------- staleness

    def record_sources(self, entry: OpenDocument) -> None:
        """Remember which revision of each referenced part this was built on."""
        entry.sources = {}
        for path in entry.references:
            source = self.by_path(path)
            if source is not None:
                entry.sources[os.path.normcase(path)] = source.published

    def stale_sources(self, entry: OpenDocument) -> List[OpenDocument]:
        """Open parts this document refers to that have been edited since."""
        out: List[OpenDocument] = []
        for path in entry.references:
            source = self.by_path(path)
            if source is None or not source.is_part:
                continue
            if source.revision != source.published:
                out.append(source)
                continue
            seen = entry.sources.get(os.path.normcase(path))
            if seen is not None and seen != source.published:
                out.append(source)
        return out

    def is_stale(self, entry: OpenDocument) -> bool:
        return bool(self.stale_sources(entry))

    def dependents_of(self, entry: OpenDocument) -> List[OpenDocument]:
        """Open assemblies and sheets that place this document."""
        if not entry.path:
            return []
        return [e for e in self.documents
                if e is not entry
                and any(same_file(p, entry.path) for p in e.references)]

    # ------------------------------------------------------------- tab data

    def tab_entries(self) -> List[Dict[str, Any]]:
        return [{
            "key": e.key,
            "title": e.label,
            "type": e.doc_type,
            "modified": e.modified,
            "stale": self.is_stale(e),
            "tooltip": e.path or "Not saved yet",
        } for e in self.documents]
