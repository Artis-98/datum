"""The DATUM container: archives, manifests, versions, migration, links."""
import json
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


from datum import APP_NAME  # noqa: E402
from datum.core import fileformat, kernel  # noqa: E402
from datum.core.assembly import (  # noqa: E402
    AssemblyDocument, DrawingDocument, open_any,
)
from datum.core.document import Document  # noqa: E402
from datum.core.features import ExtrudeFeature, SketchFeature  # noqa: E402
from datum.core.fileformat import (  # noqa: E402
    FileFormatError, UnsupportedVersionError,
)
from datum.core.sketch import STANDARD_PLANES, Sketch  # noqa: E402

FAILS = []
WORK = tempfile.mkdtemp(prefix="datum_fmt_")


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def make_part(width=60.0, height=40.0, thickness=10.0):
    doc = Document()
    sk = SketchFeature()
    sk.name = "Outline"
    sk.sketch = Sketch(STANDARD_PLANES["XY"], "Outline")
    sk.sketch.add_rectangle((0, 0), (width, height))
    doc.add_feature(sk)
    ex = ExtrudeFeature()
    ex.sketch_id = sk.id
    ex.distance = str(thickness)
    doc.add_feature(ex)
    doc.rebuild()
    return doc


PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)

# ==========================================================================
print("extensions follow Inventor's convention")
check("part is .pdat", fileformat.EXTENSIONS["part"] == ".pdat")
check("assembly is .adat", fileformat.EXTENSIONS["assembly"] == ".adat")
check("drawing is .ddat", fileformat.EXTENSIONS["drawing"] == ".ddat")
check("extension is added on save",
      fileformat.ensure_extension(os.path.join(WORK, "a"), "part")
      == os.path.join(WORK, "a.pdat"))
check("a wrong DATUM extension is corrected",
      fileformat.ensure_extension(os.path.join(WORK, "a.adat"), "part")
      == os.path.join(WORK, "a.pdat"))
check("a right one is left alone",
      fileformat.ensure_extension(os.path.join(WORK, "a.pdat"), "part")
      == os.path.join(WORK, "a.pdat"))
check("type inferred from the path",
      fileformat.type_for_path("x.adat") == "assembly")

print("dialog filters name the right extension")
check("part save filter", ".pdat" in fileformat.save_filter("part"),
      fileformat.save_filter("part"))
check("assembly save filter", ".adat" in fileformat.save_filter("assembly"))
check("drawing save filter", ".ddat" in fileformat.save_filter("drawing"))
combined = fileformat.open_filter()
check("open filter lists all three",
      all(e in combined for e in (".pdat", ".adat", ".ddat")), combined)
check("open filter offers legacy files", ".forge" in combined)
check("narrowed filter is only that type",
      ".adat" not in fileformat.open_filter("part"),
      fileformat.open_filter("part"))

# ==========================================================================
print("a part saves as a real ZIP archive")
doc = make_part()
volume = kernel.volume(doc.shape)
path = doc.save(os.path.join(WORK, "bracket"), thumbnail=PNG)
check("saved with the .pdat extension", path.endswith(".pdat"), path)
check("the file is a ZIP", zipfile.is_zipfile(path))

with zipfile.ZipFile(path) as archive:
    names = set(archive.namelist())
    check("holds a manifest", "manifest.json" in names, names)
    check("holds the model", "geometry.json" in names, names)
    check("holds a thumbnail", "thumbnail.png" in names, names)
    manifest = json.loads(archive.read("manifest.json"))
    info = {i.filename: i for i in archive.infolist()}
    check("json is lightly compressed",
          info["geometry.json"].compress_type == zipfile.ZIP_DEFLATED)
    check("the png is stored, not re-compressed",
          info["thumbnail.png"].compress_type == zipfile.ZIP_STORED)
    check("thumbnail bytes survived",
          archive.read("thumbnail.png") == PNG)

print("the manifest carries what it must")
for key in ("format", "version", "type", "units", "created", "modified"):
    check("manifest has %s" % key, key in manifest, sorted(manifest))
check("format is DATUM", manifest["format"] == "DATUM", manifest["format"])
check("version is an integer", isinstance(manifest["version"], int))
check("version starts at 1", manifest["version"] == 1, manifest["version"])
check("type is part", manifest["type"] == "part")
check("units are mm", manifest["units"] == "mm")
check("created is ISO 8601", "T" in manifest["created"], manifest["created"])
check("modified is ISO 8601", "T" in manifest["modified"])
check("the writing application is recorded",
      APP_NAME in manifest.get("application", ""), manifest.get("application"))

print("and it round-trips")
reloaded = Document.load(path)
check("geometry survived",
      abs(kernel.volume(reloaded.shape) - volume) < 1e-6,
      kernel.volume(reloaded.shape))
check("features survived", len(reloaded.features) == len(doc.features))
check("created timestamp preserved", reloaded.created == manifest["created"])
check("thumbnail comes back", reloaded.thumbnail == PNG)
check("no migration was needed", reloaded.migrated_from is None)
check("peek reads the manifest alone",
      fileformat.peek(path).type == "part")
check("thumbnail_of pulls the preview", fileformat.thumbnail_of(path) == PNG)

print("saving twice keeps created but moves modified")
first = fileformat.peek(path)
doc.save(path, thumbnail=PNG)
second = fileformat.peek(path)
check("created is stable", second.created == first.created)
check("modified is refreshed or equal", second.modified >= first.modified)

# ==========================================================================
print("bad files are refused with a reason")
plain = os.path.join(WORK, "notdatum.pdat")
with open(plain, "w", encoding="utf-8") as handle:
    handle.write("just some text")
try:
    fileformat.peek(plain)
    check("a non-archive is rejected", False)
except FileFormatError as exc:
    check("a non-archive is rejected", "not a DATUM document" in str(exc),
          str(exc))

no_manifest = os.path.join(WORK, "nomanifest.pdat")
with zipfile.ZipFile(no_manifest, "w") as archive:
    archive.writestr("geometry.json", "{}")
try:
    fileformat.peek(no_manifest)
    check("an archive with no manifest is rejected", False)
except FileFormatError as exc:
    check("an archive with no manifest is rejected", "manifest" in str(exc),
          str(exc))

wrong_format = os.path.join(WORK, "wrongformat.pdat")
with zipfile.ZipFile(wrong_format, "w") as archive:
    archive.writestr("manifest.json", json.dumps(
        {"format": "SOMETHINGELSE", "version": 1, "type": "part"}))
    archive.writestr("geometry.json", "{}")
try:
    fileformat.peek(wrong_format)
    check("a foreign format is rejected", False)
except FileFormatError as exc:
    check("a foreign format is rejected", "not a DATUM document" in str(exc),
          str(exc))

missing_format = os.path.join(WORK, "noformat.pdat")
with zipfile.ZipFile(missing_format, "w") as archive:
    archive.writestr("manifest.json", json.dumps({"version": 1,
                                                  "type": "part"}))
    archive.writestr("geometry.json", "{}")
try:
    fileformat.peek(missing_format)
    check("a missing format field is rejected", False)
except FileFormatError as exc:
    check("a missing format field is rejected", "(missing)" in str(exc),
          str(exc))

print("a newer schema is refused, not half-read")
future = os.path.join(WORK, "future.pdat")
with zipfile.ZipFile(future, "w") as archive:
    archive.writestr("manifest.json", json.dumps({
        "format": "DATUM", "version": fileformat.SCHEMA_VERSION + 5,
        "type": "part", "units": "mm", "created": "x", "modified": "x"}))
    archive.writestr("geometry.json", json.dumps({"features": []}))
try:
    fileformat.read(future)
    check("a newer file is refused", False)
except UnsupportedVersionError as exc:
    message = str(exc)
    check("a newer file is refused", True)
    check("the message says why",
          "newer version" in message and "Update DATUM" in message, message)

print("a part cannot be opened as an assembly, or the reverse")
try:
    fileformat.read(path, expected_type="assembly")
    check("type mismatch is caught", False)
except FileFormatError as exc:
    check("type mismatch is caught", "is a Part, not a Assembly" in str(exc),
          str(exc))

# ==========================================================================
print("older files migrate forward")
legacy = os.path.join(WORK, "old.forge")   # a genuine pre-DATUM file
legacy_doc = make_part(80.0, 30.0, 5.0)
with open(legacy, "w", encoding="utf-8") as handle:
    payload = legacy_doc.to_dict()
    payload["format"] = "forge-part"
    payload["version"] = 1
    json.dump(payload, handle)

opened = Document.load(legacy)
check("a legacy .forge part opens", opened.shape is not None)
check("its geometry is intact",
      abs(kernel.volume(opened.shape)
          - kernel.volume(legacy_doc.shape)) < 1e-6)
check("it reports being migrated", opened.migrated_from == 0,
      opened.migrated_from)
check("it has no path, so Save prompts for a new one", opened.path == "",
      opened.path)
check("and it is marked modified", opened.modified)

converted = opened.save(os.path.join(WORK, "converted"), thumbnail=PNG)
check("it saves out as a .pdat", converted.endswith(".pdat"), converted)
check("the converted file is valid",
      fileformat.peek(converted).version == fileformat.SCHEMA_VERSION)

print("the migration step itself is explicit")
migrated = fileformat.migrate({"features": []}, 0, "part")
check("v0 gains the fields v1 expects",
      "hidden_planes" in migrated and "origin_autohidden" in migrated,
      sorted(migrated))
check("v0 loses the old format marker", "format" not in migrated)
try:
    fileformat.migrate({}, 99, "part")
    check("an impossible migration is an error", False)
except FileFormatError as exc:
    check("an impossible migration is an error", "no migration path" in str(exc))

# ==========================================================================
print("assemblies reference parts by relative path")
project = os.path.join(WORK, "project")
parts_dir = os.path.join(project, "parts")
os.makedirs(parts_dir, exist_ok=True)

left = make_part(40, 20, 5).save(os.path.join(parts_dir, "left"),
                                 thumbnail=PNG)
right = make_part(30, 20, 5).save(os.path.join(parts_dir, "right"),
                                  thumbnail=PNG)

asm = AssemblyDocument()
asm.path = os.path.join(project, "frame.adat")
ref_left = asm.add_component(left, "Left Plate")
asm.add_component(right, "Right Plate")
check("relative path is stored", ref_left.path == "parts/left.pdat",
      ref_left.path)
check("the file name is stored too", ref_left.name == "left.pdat",
      ref_left.name)
check("a label is kept", ref_left.label == "Left Plate")

asm_path = asm.save(asm.path, thumbnail=PNG)
check("assembly saved as .adat", asm_path.endswith(".adat"), asm_path)
asm_manifest = fileformat.peek(asm_path)
check("its manifest says assembly", asm_manifest.type == "assembly")
check("the manifest lists its references",
      len(asm_manifest.references) == 2, asm_manifest.references)

back = AssemblyDocument.load(asm_path)
check("components round-trip", len(back.components) == 2)
check("paths round-trip", back.components[0].path == "parts/left.pdat")
check("every link resolves", back.broken_links() == [], back.broken_links())
resolved = back.resolved_components()
check("components resolve to real files",
      all(p and os.path.exists(p) for _r, p in resolved), resolved)

print("broken links are reported clearly")
os.remove(right)
broken = back.broken_links()
check("the missing part is reported", len(broken) == 1, broken)
check("it names the file", broken[0].reference.name == "right.pdat",
      broken[0].reference.name)
check("and says what is wrong", "not found" in broken[0].reason,
      broken[0].reason)
check("the description is readable",
      "right.pdat" in broken[0].describe(), broken[0].describe())

print("a moved part is found again by its remembered name")
moved = make_part(30, 20, 5).save(os.path.join(project, "right"),
                                  thumbnail=PNG)
check("resolves via the stored name",
      back.components[1].resolve(back.base_dir) == moved,
      back.components[1].resolve(back.base_dir))
check("so it is no longer broken", back.broken_links() == [])

print("a reference to the wrong kind of file is reported")
drawing = DrawingDocument()
drawing.path = os.path.join(project, "sheet.ddat")
drawing.set_source(left)
drawing_path = drawing.save(drawing.path, thumbnail=PNG)
bad_asm = AssemblyDocument()
bad_asm.path = os.path.join(project, "bad.adat")
bad_asm.add_component(drawing_path)
issues = bad_asm.broken_links()
check("a drawing cannot be a component", len(issues) == 1, issues)
check("and the reason says so", "Drawing" in issues[0].reason,
      issues[0].reason)

print("drawings are documents in their own right")
sheet = DrawingDocument.load(drawing_path)
check("drawing round-trips", sheet.sheet_size == "A3")
check("its source is remembered", sheet.source.name == "left.pdat",
      sheet.source.name)
check("its manifest type is drawing",
      fileformat.peek(drawing_path).type == "drawing")
check("its source link resolves", sheet.broken_links() == [],
      sheet.broken_links())

print("open_any picks the right document class")
check("part", isinstance(open_any(path), Document))
check("assembly", isinstance(open_any(asm_path), AssemblyDocument))
check("drawing", isinstance(open_any(drawing_path), DrawingDocument))

print("saving never destroys the previous file on failure")
guarded = os.path.join(WORK, "guarded.pdat")
make_part().save(guarded, thumbnail=PNG)
before = os.path.getsize(guarded)
try:
    fileformat.write(guarded, "part", {"bad": {1, 2}})  # a set is not JSON
except Exception:
    pass
check("the old file is untouched", os.path.getsize(guarded) == before,
      os.path.getsize(guarded))
check("no half-written temp file is left",
      not os.path.exists(guarded + ".saving"))
check("and it still opens", Document.load(guarded).shape is not None)

print()
if FAILS:
    print("%d FAILURES: %s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("all file format tests passed")
sys.exit(0)
