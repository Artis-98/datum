"""The DATUM document container.

A DATUM file is a ZIP archive, not a blob, so anything that can read a ZIP
can inspect it - including the OS shell, which is why the thumbnail lives
inside as a plain PNG.

    manifest.json    metadata: format, version, type, units, timestamps
    geometry.json    the model itself
    thumbnail.png    256x256 preview rendered at save time

Four document types, following Inventor's convention:

    .pdat   part
    .adat   assembly
    .ddat   drawing
    .cdat   CAM sheet

Reading always starts with the manifest: a file that does not declare itself
as DATUM is rejected outright, and one written by a newer schema is refused
rather than half-parsed.  Older schemas are migrated forward on load.
"""

from __future__ import annotations

import datetime
import io
import json
import os
import zipfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

FORMAT = "DATUM"
SCHEMA_VERSION = 1

MANIFEST_NAME = "manifest.json"
GEOMETRY_NAME = "geometry.json"
THUMBNAIL_NAME = "thumbnail.png"
THUMBNAIL_SIZE = 256

PART = "part"
ASSEMBLY = "assembly"
DRAWING = "drawing"
CAM = "cam"
DOCUMENT_TYPES = (PART, ASSEMBLY, DRAWING, CAM)

EXTENSIONS = {PART: ".pdat", ASSEMBLY: ".adat", DRAWING: ".ddat",
              CAM: ".cdat"}
TYPE_BY_EXTENSION = {ext: kind for kind, ext in EXTENSIONS.items()}

TYPE_LABELS = {PART: "Part", ASSEMBLY: "Assembly", DRAWING: "Drawing",
               CAM: "CAM Sheet"}

# JSON compresses very well and is cheap to deflate at level 1; the PNG is
# already compressed, so it is stored as-is. Saves stay fast either way.
JSON_COMPRESSION = zipfile.ZIP_DEFLATED
JSON_COMPRESS_LEVEL = 1
BLOB_COMPRESSION = zipfile.ZIP_STORED

LEGACY_EXTENSION = ".forge"


class FileFormatError(Exception):
    """The file is not a readable DATUM document."""


class UnsupportedVersionError(FileFormatError):
    """The file was written by a newer build than this one."""


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds")


def extension_for(doc_type: str) -> str:
    return EXTENSIONS.get(doc_type, EXTENSIONS[PART])


def type_for_path(path: str) -> Optional[str]:
    return TYPE_BY_EXTENSION.get(os.path.splitext(path)[1].lower())


def ensure_extension(path: str, doc_type: str) -> str:
    """Give a path the right extension for its document type."""
    wanted = extension_for(doc_type)
    stem, ext = os.path.splitext(path)
    if ext.lower() == wanted:
        return path
    if ext.lower() in TYPE_BY_EXTENSION or ext.lower() == LEGACY_EXTENSION:
        return stem + wanted        # replace a wrong DATUM extension
    return path + wanted


def save_filter(doc_type: str) -> str:
    return "DATUM %s (*%s)" % (TYPE_LABELS.get(doc_type, "Part"),
                               extension_for(doc_type))


def open_filter(doc_type: Optional[str] = None) -> str:
    """Dialog filter, narrowed to one type or listing all of them."""
    if doc_type:
        return "%s;;All files (*)" % save_filter(doc_type)
    everything = " ".join("*%s" % extension_for(k) for k in DOCUMENT_TYPES)
    foreign = "*.step *.stp *.iges *.igs"
    parts = ["Everything DATUM opens (%s %s)" % (everything, foreign),
             "DATUM documents (%s)" % everything]
    parts += [save_filter(k) for k in DOCUMENT_TYPES]
    parts.append("STEP and IGES, from other CAD systems (%s)" % foreign)
    parts.append("Legacy FORGE part (*%s)" % LEGACY_EXTENSION)
    parts.append("All files (*)")
    return ";;".join(parts)


# --------------------------------------------------------------------------
# reading and writing
# --------------------------------------------------------------------------


@dataclass
class Manifest:
    format: str = FORMAT
    version: int = SCHEMA_VERSION
    type: str = PART
    units: str = "mm"
    created: str = ""
    modified: str = ""
    application: str = ""
    references: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "format": self.format,
            "version": self.version,
            "type": self.type,
            "units": self.units,
            "created": self.created or now(),
            "modified": self.modified or now(),
        }
        if self.application:
            data["application"] = self.application
        if self.references:
            data["references"] = self.references
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Manifest":
        return cls(
            format=str(data.get("format", "")),
            version=int(data.get("version", 0)),
            type=str(data.get("type", PART)),
            units=str(data.get("units", "mm")),
            created=str(data.get("created", "")),
            modified=str(data.get("modified", "")),
            application=str(data.get("application", "")),
            references=list(data.get("references", [])),
        )


@dataclass
class DatumFile:
    manifest: Manifest
    geometry: Dict[str, Any]
    thumbnail: Optional[bytes] = None
    path: str = ""
    migrated_from: Optional[int] = None
    # further JSON members a document type wanted kept separate from its
    # model data - a drawing's generated views, for instance, which are
    # bulky, derived, and better not tangled up with what was authored
    extra: Dict[str, Any] = field(default_factory=dict)


def write(path: str, doc_type: str, geometry: Dict[str, Any],
          units: str = "mm", thumbnail: Optional[bytes] = None,
          created: str = "", application: str = "",
          references: Optional[List[Dict[str, Any]]] = None,
          extra: Optional[Dict[str, Any]] = None) -> str:
    """Write a DATUM archive, replacing any file already at ``path``."""
    if doc_type not in DOCUMENT_TYPES:
        raise FileFormatError("unknown document type %r" % doc_type)

    path = ensure_extension(path, doc_type)
    manifest = Manifest(
        type=doc_type, units=units, created=created or now(), modified=now(),
        application=application, references=list(references or []),
    )

    # write beside the target then swap, so a failure never eats the old file
    temporary = path + ".saving"
    try:
        with zipfile.ZipFile(temporary, "w") as archive:
            archive.writestr(
                MANIFEST_NAME,
                json.dumps(manifest.to_dict(), indent=1),
                compress_type=JSON_COMPRESSION,
                compresslevel=JSON_COMPRESS_LEVEL)
            archive.writestr(
                GEOMETRY_NAME,
                json.dumps(geometry, indent=1),
                compress_type=JSON_COMPRESSION,
                compresslevel=JSON_COMPRESS_LEVEL)
            for name, payload in (extra or {}).items():
                archive.writestr(
                    str(name), json.dumps(payload, indent=1),
                    compress_type=JSON_COMPRESSION,
                    compresslevel=JSON_COMPRESS_LEVEL)
            if thumbnail:
                archive.writestr(THUMBNAIL_NAME, thumbnail,
                                 compress_type=BLOB_COMPRESSION)
        os.replace(temporary, path)
    except Exception:
        if os.path.exists(temporary):
            try:
                os.remove(temporary)
            except OSError:
                pass
        raise
    return path


def peek(path: str) -> Manifest:
    """Read just the manifest - enough to decide whether a file is openable."""
    if not os.path.exists(path):
        raise FileFormatError("file not found: %s" % path)
    if not zipfile.is_zipfile(path):
        raise FileFormatError(
            "%s is not a DATUM document (expected a DATUM archive)"
            % os.path.basename(path))
    try:
        with zipfile.ZipFile(path, "r") as archive:
            raw = archive.read(MANIFEST_NAME)
    except KeyError:
        raise FileFormatError(
            "%s has no manifest, so it is not a DATUM document"
            % os.path.basename(path)) from None
    except zipfile.BadZipFile as exc:
        raise FileFormatError("%s is damaged: %s"
                              % (os.path.basename(path), exc)) from exc

    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FileFormatError("the manifest is unreadable: %s" % exc) from exc

    manifest = Manifest.from_dict(data)
    _validate(manifest, path)
    return manifest


def _validate(manifest: Manifest, path: str) -> None:
    name = os.path.basename(path)
    if manifest.format != FORMAT:
        raise FileFormatError(
            "%s is not a DATUM document - its manifest says format %r"
            % (name, manifest.format or "(missing)"))
    if manifest.version <= 0:
        raise FileFormatError("%s has no schema version in its manifest" % name)
    if manifest.version > SCHEMA_VERSION:
        raise UnsupportedVersionError(
            "%s was written by a newer version of DATUM (file schema %d, this "
            "build reads up to %d). Update DATUM to open it."
            % (name, manifest.version, SCHEMA_VERSION))
    if manifest.type not in DOCUMENT_TYPES:
        raise FileFormatError("%s declares an unknown document type %r"
                              % (name, manifest.type))


def read(path: str, expected_type: Optional[str] = None) -> DatumFile:
    """Open a DATUM archive, migrating an older schema forward if needed."""
    if os.path.splitext(path)[1].lower() == LEGACY_EXTENSION:
        return _read_legacy(path, expected_type)

    manifest = peek(path)
    if expected_type and manifest.type != expected_type:
        raise FileFormatError(
            "%s is a %s, not a %s"
            % (os.path.basename(path),
               TYPE_LABELS.get(manifest.type, manifest.type),
               TYPE_LABELS.get(expected_type, expected_type)))

    with zipfile.ZipFile(path, "r") as archive:
        try:
            geometry_raw = archive.read(GEOMETRY_NAME)
        except KeyError:
            raise FileFormatError(
                "%s has a manifest but no model data"
                % os.path.basename(path)) from None
        thumbnail = None
        if THUMBNAIL_NAME in archive.namelist():
            thumbnail = archive.read(THUMBNAIL_NAME)
        extra: Dict[str, Any] = {}
        for name in archive.namelist():
            if name in (MANIFEST_NAME, GEOMETRY_NAME, THUMBNAIL_NAME):
                continue
            if not name.endswith(".json"):
                continue
            try:
                extra[name] = json.loads(archive.read(name).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue

    try:
        geometry = json.loads(geometry_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FileFormatError("the model data is unreadable: %s" % exc) from exc

    migrated_from = None
    if manifest.version < SCHEMA_VERSION:
        migrated_from = manifest.version
        geometry = migrate(geometry, manifest.version, manifest.type)
        manifest.version = SCHEMA_VERSION

    return DatumFile(manifest=manifest, geometry=geometry,
                     thumbnail=thumbnail, path=path,
                     migrated_from=migrated_from, extra=extra)


def _read_legacy(path: str, expected_type: Optional[str]) -> DatumFile:
    """Open a pre-DATUM .forge part, which was plain JSON on disk."""
    if expected_type and expected_type != PART:
        raise FileFormatError("legacy .forge files are always parts")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise FileFormatError("could not read %s: %s"
                              % (os.path.basename(path), exc)) from exc

    if data.get("format") != "forge-part":
        raise FileFormatError("%s is not a FORGE part file"
                              % os.path.basename(path))

    stamp = now()
    manifest = Manifest(type=PART, units=data.get("units", "mm"),
                        created=stamp, modified=stamp)
    return DatumFile(manifest=manifest, geometry=migrate(data, 0, PART),
                     thumbnail=None, path=path, migrated_from=0)


def thumbnail_of(path: str) -> Optional[bytes]:
    """The stored preview image, if the archive carries one."""
    try:
        if not zipfile.is_zipfile(path):
            return None
        with zipfile.ZipFile(path, "r") as archive:
            if THUMBNAIL_NAME in archive.namelist():
                return archive.read(THUMBNAIL_NAME)
    except (OSError, zipfile.BadZipFile):
        return None
    return None


# --------------------------------------------------------------------------
# migration
# --------------------------------------------------------------------------


def migrate(geometry: Dict[str, Any], from_version: int,
            doc_type: str = PART) -> Dict[str, Any]:
    """Bring model data written by an older schema up to the current one.

    Version 0 is the pre-DATUM ``.forge`` layout.  Each step is deliberately
    small and forward-only; a step that cannot be made is an error rather
    than a silent partial load.
    """
    data = dict(geometry)
    version = from_version

    if version < 1:
        # 0 -> 1: the old flat JSON becomes the archive's geometry payload.
        data.pop("format", None)
        data.pop("version", None)
        data.setdefault("parameters", [])
        data.setdefault("features", [])
        data.setdefault("hidden_planes", [])
        data.setdefault("origin_autohidden", False)
        version = 1

    if version != SCHEMA_VERSION:
        raise FileFormatError(
            "no migration path from schema %d to %d" % (from_version,
                                                        SCHEMA_VERSION))
    return data


# --------------------------------------------------------------------------
# assembly references
# --------------------------------------------------------------------------


@dataclass
class ComponentRef:
    """A part referenced by an assembly.

    The path is relative to the assembly file so a project folder can be
    moved as a unit; ``name`` is the file name as it was when the reference
    was made, which is what makes a broken link reportable rather than just
    missing.
    """

    path: str = ""
    name: str = ""
    label: str = ""
    transform: List[float] = field(
        default_factory=lambda: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    suppressed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "name": self.name, "label": self.label,
                "transform": list(self.transform),
                "suppressed": self.suppressed}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ComponentRef":
        return cls(path=str(data.get("path", "")),
                   name=str(data.get("name", "")),
                   label=str(data.get("label", "")),
                   transform=[float(v) for v in
                              data.get("transform", [0, 0, 0, 0, 0, 0])],
                   suppressed=bool(data.get("suppressed", False)))

    def resolve(self, base_dir: str) -> Optional[str]:
        """Absolute path of the referenced file, or None when it is gone."""
        if not self.path:
            return None
        candidate = os.path.normpath(os.path.join(base_dir, self.path))
        if os.path.exists(candidate):
            return candidate
        # the folder may have been reorganised; look for the remembered name
        if self.name:
            nearby = os.path.join(base_dir, self.name)
            if os.path.exists(nearby):
                return nearby
        return None


@dataclass
class BrokenLink:
    reference: ComponentRef
    reason: str

    def describe(self) -> str:
        return "%s - %s" % (self.reference.name or self.reference.path
                            or "(unnamed)", self.reason)


def check_links(references: List[ComponentRef], base_dir: str,
                allowed: Optional[Tuple[str, ...]] = None) -> List[BrokenLink]:
    """Report every reference that cannot be opened, and why.

    ``allowed`` is the set of document types the referring file can use: an
    assembly takes parts and sub-assemblies, a CAM sheet takes parts only.
    """
    allowed = allowed or (PART, ASSEMBLY)
    broken: List[BrokenLink] = []
    for ref in references:
        if ref.suppressed:
            continue
        resolved = ref.resolve(base_dir)
        if resolved is None:
            broken.append(BrokenLink(
                ref, "file not found at %s" % (ref.path or "(no path)")))
            continue
        try:
            manifest = peek(resolved)
        except UnsupportedVersionError as exc:
            broken.append(BrokenLink(ref, str(exc)))
            continue
        except FileFormatError as exc:
            broken.append(BrokenLink(ref, str(exc)))
            continue
        if manifest.type not in allowed:
            broken.append(BrokenLink(
                ref, "is a %s, and only %s can be used here"
                     % (TYPE_LABELS.get(manifest.type, manifest.type),
                        " or ".join(TYPE_LABELS.get(k, k).lower()
                                    for k in allowed))))
    return broken


def relative_path(target: str, base_dir: str) -> str:
    """Path of ``target`` relative to the assembly, keeping it portable."""
    try:
        return os.path.relpath(target, base_dir).replace(os.sep, "/")
    except ValueError:
        # different drive on Windows - an absolute path is the only option
        return target.replace(os.sep, "/")
