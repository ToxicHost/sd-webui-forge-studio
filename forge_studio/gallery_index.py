"""Reading what is on disk: the walk, and one image's facts.

The half of the Gallery that touches files. `gallery_store` keeps the index and
`gallery_metadata` parses a parameters block; this module finds the files and
gets that block out of them.

Three pieces of the Extension's behaviour are preserved deliberately.

**The walk follows links.** `os.walk` defaults to `followlinks=False`, which
silently skips symlinked DIRECTORIES -- symlinked files list fine, so the
failure looks like a few missing images rather than a missing folder. Owners
keep output on other drives and link it in, so a Gallery that cannot see
through a link cannot see their pictures. (Windows junctions were never
affected; Python does not classify them as links.) A realpath guard makes link
cycles terminate and lists a directory reachable by two paths only once.

**The EXIF key names are a contract.** `gallery.js` renders unrecognised
metadata as an "other" list, filtered by a fixed set of snake_case keys --
`pixel_x`, `sensing_method`, `max_aperture` and the rest. Emitting Pillow's own
tag names instead would leave every one of them unfiltered, so the details
panel would fill with camera noise. The table below is that skip-list's other
half, ported name for name.

**A broken file is data, not an exception.** A scan crosses tens of thousands
of files and some are truncated, some are locked by another process, and some
are not really images. Per-file failure is recorded on the row and the scan
goes on. A MISSING PILLOW is a different thing entirely -- that is a capability
the Gallery does not have, and it raises rather than quietly reporting that
every image on the machine has no metadata.

Studio-owned: nothing here imports Forge, Neo or Torch.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Iterator

from .gallery_metadata import parse_generation_parameters, search_text_for

#: What the Gallery will index. Videos carry no parameters block but do belong
#: in a gallery of generated output.
IMAGE_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".tif", ".avif",
})
VIDEO_SUFFIXES = frozenset({".mp4", ".webm", ".mkv", ".mov", ".gif"})
MEDIA_SUFFIXES = IMAGE_SUFFIXES | VIDEO_SUFFIXES

#: Caps, from the Extension. A metadata field is not a document store.
_RAW_LIMIT = 3000
_VALUE_LIMIT = 500
_TEXT_LIMIT = 2000

#: EXIF tag id -> the name `gallery.js` filters on. Ported name for name; see
#: the module docstring. Changing one of these makes that tag appear in the
#: owner's details panel.
_EXIF_NAMES = {
    0x010F: "camera_make", 0x0110: "camera_model", 0x0131: "software",
    0x0132: "date_time", 0x9003: "date_original", 0x9004: "date_digitized",
    0x829A: "exposure_time", 0x829D: "f_number", 0x8827: "iso",
    0x9207: "metering_mode", 0x9209: "flash", 0x920A: "focal_length",
    0xA405: "focal_length_35mm", 0xA001: "color_space",
    0x8822: "exposure_program", 0x9201: "shutter_speed",
    0x9202: "aperture", 0x9204: "exposure_bias",
    0x9206: "subject_distance", 0xA002: "pixel_x", 0xA003: "pixel_y",
    0xA217: "sensing_method", 0xA403: "white_balance",
    0xA406: "scene_type", 0xA431: "serial_number",
    0xA432: "lens_info", 0xA433: "lens_make", 0xA434: "lens_model",
    0xA300: "file_source", 0x9286: "user_comment",
    0x0112: "orientation", 0x011A: "x_resolution", 0x011B: "y_resolution",
    0x0128: "resolution_unit", 0xA404: "digital_zoom",
    0x9205: "max_aperture", 0xA402: "exposure_mode",
    0x8824: "spectral_sensitivity", 0xA407: "gain_control",
    0xA408: "contrast", 0xA409: "saturation", 0xA40A: "sharpness",
}

#: Pointers to other IFDs, not values an owner wants to read.
_EXIF_SKIP = frozenset({0x8769, 0x8825, 0xA005})

#: How the various writers announce an encoding before the text they wrote.
_COMMENT_PREFIXES = ("ASCII\x00\x00\x00", "UNICODE\x00", 'charset="Ascii" ')

#: PNG text keys handled by name; anything else is carried through as-is.
_HANDLED_PNG_KEYS = frozenset({
    "parameters", "prompt", "workflow", "comment", "description",
})


class ImagingUnavailable(RuntimeError):
    """Pillow is not installed, so images cannot be read at all."""


def imaging_available() -> bool:
    """Whether this Studio can read image files.

    Reported rather than assumed: the Gallery capability turns this into
    DISABLE WITH REASON, so an owner is told the Gallery is off because Pillow
    is missing instead of being shown an empty one.
    """

    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


def _require_imaging() -> Any:
    try:
        from PIL import Image
    except ImportError as error:
        raise ImagingUnavailable(
            "The Gallery cannot read images because Pillow is not installed."
        ) from error
    return Image


# -- the walk -------------------------------------------------------------


def walk_follow(root: str | Path) -> Iterator[tuple[str, list[str], list[str]]]:
    """`os.walk` that descends symlinked directories, each one exactly once.

    Yields os.walk's own tuples, and `dirnames` is os.walk's own list, so a
    caller pruning it in the loop still steers the traversal.
    """

    seen: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(str(root), followlinks=True):
        real = os.path.realpath(dirpath)
        if real in seen:
            del dirnames[:]  # reached by another path already
            continue
        seen.add(real)
        yield dirpath, dirnames, filenames


def scan_entries(root: str | Path,
                 suffixes: frozenset[str] = MEDIA_SUFFIXES
                 ) -> Iterator[tuple[Path, Any]]:
    """Every media file under `root`, WITH the stat the listing already had.

    `os.walk` is built on `os.scandir` and throws away the file information
    the directory listing already carried, so a caller that then asks for a
    size or a date pays for a fresh `stat` per file. On Windows that is a real
    syscall each time; keeping the `DirEntry` instead makes it free:

    ```text
    os.walk + os.stat per file    0.674 s   for 17,000 files
    os.scandir, DirEntry.stat()   0.029 s   -- 23x
    ```

    That is the difference between a scan that feels instant and one an owner
    counts seconds through, and it is larger on a real disk than in a
    benchmark because every one of those saved syscalls is also a chance for a
    virus scanner to look at the file.

    Follows symlinked directories and visits each real directory once, exactly
    as `walk_follow` does. Sorted at every level, so a scan is reproducible.
    """

    seen: set[str] = set()

    def descend(directory: str) -> Iterator[tuple[Path, Any]]:
        real = os.path.realpath(directory)
        if real in seen:
            return  # a cycle, or a second link to the same target
        seen.add(real)
        try:
            with os.scandir(directory) as entries:
                listing = sorted(entries, key=lambda entry: entry.name)
        except OSError:
            return  # unreadable, unplugged, or gone since the walk began
        directories: list[str] = []
        for entry in listing:
            try:
                if entry.is_dir(follow_symlinks=True):
                    directories.append(entry.path)
                elif os.path.splitext(entry.name)[1].lower() in suffixes:
                    yield Path(entry.path), entry
            except OSError:
                continue
        for child in directories:
            yield from descend(child)

    yield from descend(str(root))


def iter_media(root: str | Path,
               suffixes: frozenset[str] = MEDIA_SUFFIXES) -> Iterator[Path]:
    """Every media file under `root`, following links, in a stable order.

    Sorted at each level so a scan is reproducible and an interrupted one
    resumes over the same sequence rather than a filesystem-order shuffle.
    """

    for dirpath, dirnames, filenames in walk_follow(root):
        dirnames.sort()
        for name in sorted(filenames):
            if Path(name).suffix.lower() in suffixes:
                yield Path(dirpath) / name


# -- one file's facts -----------------------------------------------------


def file_date(path: str | Path) -> float:
    """The earliest date the filesystem admits to.

    The EARLIEST, not mtime: a copied or re-encoded file gets a fresh mtime,
    and an owner sorting by date means when the picture was made.

    `st_birthtime` is a true creation time wherever the filesystem records one.
    `st_ctime` is NOT: it is creation time under the Windows API and inode
    CHANGE time under POSIX, where a chmod moves it and it can postdate mtime
    by years. So it is consulted only where it means creation -- `os.name`,
    which selects between two genuinely different APIs, rather than a guess
    about the host.
    """

    try:
        stat = os.stat(path)
    except OSError:
        return 0.0
    dates = [stat.st_mtime] if stat.st_mtime else []
    birth = getattr(stat, "st_birthtime", None)
    if birth:
        dates.append(birth)
    if os.name == "nt" and stat.st_ctime:
        dates.append(stat.st_ctime)
    return min(dates) if dates else 0.0


def content_hash(source: Any) -> str:
    """SHA256 of the decoded RGB pixels. Empty string if it cannot be read.

    Of the PIXELS, not the file. It is the same hash after a metadata strip, a
    rename, or a re-encode to another format -- which is what lets a generated
    image be reunited with its parameters after the owner has moved it around,
    and what lets duplicate detection see through a format change.

    THREE SOURCE KINDS, ONE ALGORITHM, and the "one" is the point. A PATH is
    what the scan has when it meets a file (`facts_for`); ENCODED BYTES are
    what the delivery layer has when a generation finishes and the Gallery
    must record what made it (AR5.4); a PIL IMAGE is what a caller holding
    decoded pixels has. The generation row and the scan row are joined by
    equality of this value alone -- `gallery_service.py:1623-1630` links on
    nothing else -- so a second implementation anywhere is a row that is
    written, reports success, and silently never links.

    Bare hex, matching the Extension's `compute_content_hash`
    (`studio_gallery.py:699-717`) line for line. Not a `sha256:`-prefixed
    string: that form belongs to the mock's fixture identities and is not this.
    """

    Image = _require_imaging()
    try:
        if isinstance(source, (bytes, bytearray, memoryview)):
            source = BytesIO(bytes(source))
        if isinstance(source, (str, Path, BytesIO)):
            with Image.open(source) as image:
                return hashlib.sha256(image.convert("RGB").tobytes()).hexdigest()
        return hashlib.sha256(source.convert("RGB").tobytes()).hexdigest()
    except (OSError, ValueError):
        return ""


def _decode_comment(value: Any) -> str:
    """A UserComment as text, with its encoding announcement removed."""

    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    elif not isinstance(value, str):
        return ""
    for prefix in _COMMENT_PREFIXES:
        if value.startswith(prefix):
            value = value[len(prefix):]
    return value.strip("\x00").strip()


def _absorb_parameters(metadata: dict[str, Any], text: str) -> None:
    """Record a parameters block and everything parsed out of it.

    First writer wins. PNG text is read before EXIF, so an image carrying both
    keeps the PNG block -- that is the one Forge itself wrote.
    """

    if not text or "raw_parameters" in metadata:
        return
    metadata["raw_parameters"] = text[:_RAW_LIMIT]
    metadata.update(parse_generation_parameters(text))


def _read_novelai_comment(raw: Any, metadata: dict[str, Any]) -> None:
    """NovelAI writes its settings as JSON in a `Comment` chunk."""

    try:
        comment = json.loads(raw)
    except (ValueError, TypeError):
        return
    if not isinstance(comment, dict):
        return
    if "uc" in comment:  # their name for the negative prompt
        metadata["negative_prompt"] = str(comment["uc"])[:1000]
    for key in ("steps", "sampler", "seed"):
        if key in comment:
            metadata[key] = comment[key]
    if "strength" in comment:
        metadata["cfg_scale"] = comment["strength"]


def _read_png_text(image: Any, metadata: dict[str, Any]) -> None:
    text = getattr(image, "text", None) or {}
    if "parameters" in text:
        _absorb_parameters(metadata, text["parameters"])
    if "prompt" in text:
        try:
            json.loads(text["prompt"])
        except (ValueError, TypeError):
            metadata.setdefault("prompt", text["prompt"][:_TEXT_LIMIT])
        else:
            # ComfyUI keeps a whole workflow graph under `prompt`. Flagged, not
            # parsed: it is JSON, not a parameters block, and putting it through
            # the A1111 parser produces nonsense in the prompt field.
            metadata["comfyui_prompt"] = True
            metadata["raw_parameters"] = text["prompt"][:_TEXT_LIMIT]
    if "workflow" in text:
        metadata["comfyui_workflow"] = True
    if "Description" in text:
        metadata.setdefault("prompt", text["Description"][:_TEXT_LIMIT])
    if "Comment" in text:
        _read_novelai_comment(text["Comment"], metadata)
    for key, value in text.items():
        lowered = key.lower()
        if lowered in _HANDLED_PNG_KEYS or lowered in metadata:
            continue
        if isinstance(value, str) and len(value) < _TEXT_LIMIT:
            metadata[key] = value


def _read_exif_entries(entries: Any, metadata: dict[str, Any],
                       fallback: str) -> None:
    from PIL.ExifTags import TAGS

    for tag, value in entries.items():
        if tag in _EXIF_SKIP:
            continue
        name = _EXIF_NAMES.get(tag) or TAGS.get(tag) or f"{fallback}{tag}"
        if name == "user_comment":
            # Where Forge writes the parameters block for JPEG and WebP, which
            # have no PNG text chunk to put it in.
            _absorb_parameters(metadata, _decode_comment(value))
            continue
        if isinstance(value, bytes):
            continue  # a blob, not something to show an owner
        if (isinstance(value, tuple) and len(value) == 2
                and all(isinstance(part, (int, float)) for part in value)):
            value = f"{value[0]}/{value[1]}" if value[1] else str(value[0])
        text = str(value).strip()
        if text and len(text) < _VALUE_LIMIT and name not in metadata:
            metadata[name] = text


def _read_exif(image: Any, metadata: dict[str, Any]) -> None:
    from PIL.ExifTags import IFD

    exif = image.getexif() if hasattr(image, "getexif") else None
    if not exif:
        return
    _read_exif_entries(exif, metadata, "tag_")
    try:
        sub = exif.get_ifd(IFD.Exif)
    except (KeyError, ValueError, OSError):
        return
    if sub:
        _read_exif_entries(sub, metadata, "exif_")


def read_metadata(path: str | Path) -> dict[str, Any]:
    """Everything embedded in one file. Never raises for a bad file.

    A truncated or locked file returns `{"error": ...}` alongside whatever was
    read before the failure, because one bad file in a folder of forty thousand
    must not end a scan.
    """

    Image = _require_imaging()
    metadata: dict[str, Any] = {}
    try:
        with Image.open(path) as image:
            if Path(path).suffix.lower() == ".png":
                _read_png_text(image, metadata)
            _read_exif(image, metadata)
    except (OSError, ValueError, TypeError) as error:
        metadata["error"] = f"{type(error).__name__}: {error}"
    try:
        metadata.setdefault("file_size", os.path.getsize(path))
    except OSError:
        pass
    return metadata


@dataclass(frozen=True)
class IndexedFile:
    """One file as the index will store it."""

    path: Path
    width: int = 0
    height: int = 0
    date: float = 0.0
    size: int = 0
    content_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    search_text: str = ""

    @property
    def readable(self) -> bool:
        """Whether the file opened. False is still indexed -- an owner should
        see that a broken picture is there rather than have it vanish."""

        return "error" not in self.metadata


def outline(path: str | Path) -> IndexedFile:
    """What a file is, without decoding it. Microseconds, not milliseconds.

    THE FAST PATH, and the one a scan uses. Pillow reads an image's dimensions
    out of its header without touching the pixels, so this costs a `stat` and a
    header parse -- about 0.1ms against the 26ms a full read takes. Across
    seventeen thousand images that is the difference between four seconds and
    twelve minutes before the owner sees their library.

    Everything expensive -- the parameters, the pixel hash, the perceptual hash
    -- is left to `enrich`, which runs in the background afterwards.
    """

    Image = _require_imaging()
    path = Path(path)
    width = height = 0
    try:
        with Image.open(path) as image:
            width, height = image.size
    except (OSError, ValueError, TypeError):
        pass
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return IndexedFile(path=path, width=width, height=height,
                       date=file_date(path), size=size)


def enrich(path: str | Path, *, with_hash: bool = True,
           image: Any = None) -> IndexedFile:
    """Everything about one file, from ONE decode.

    The three expensive facts -- embedded parameters, the pixel hash and the
    dimensions -- used to cost three separate `Image.open` calls and two full
    decodes. Opening once and deriving all of them from the same image is most
    of the saving; deferring this pass out of the scan is the rest.

    `image` lets a caller that has ALREADY opened the file hand it over, so the
    enrichment worker computes the perceptual hash from the same decode instead
    of paying for a second one. It is not closed here -- whoever opened it
    still owns it.
    """

    Image = _require_imaging()
    path = Path(path)
    metadata: dict[str, Any] = {}
    width = height = 0
    digest = ""

    def derive(opened: Any) -> None:
        nonlocal width, height, digest
        width, height = opened.size
        if path.suffix.lower() == ".png":
            _read_png_text(opened, metadata)
        _read_exif(opened, metadata)
        if with_hash:
            try:
                digest = hashlib.sha256(
                    opened.convert("RGB").tobytes()
                ).hexdigest()
            except (OSError, ValueError):
                digest = ""

    try:
        if image is not None:
            derive(image)
        else:
            with Image.open(path) as opened:
                derive(opened)
    except (OSError, ValueError, TypeError) as error:
        metadata["error"] = f"{type(error).__name__}: {error}"

    try:
        metadata.setdefault("file_size", path.stat().st_size)
    except OSError:
        pass

    return IndexedFile(
        path=path,
        width=width,
        height=height,
        date=file_date(path),
        size=int(metadata.get("file_size") or 0),
        content_hash=digest,
        metadata=metadata,
        search_text=search_text_for(metadata),
    )


def describe(path: str | Path, *, with_hash: bool = True) -> IndexedFile:
    """Backwards-compatible name for `enrich`."""

    return enrich(path, with_hash=with_hash)


__all__ = (
    "IMAGE_SUFFIXES",
    "MEDIA_SUFFIXES",
    "VIDEO_SUFFIXES",
    "ImagingUnavailable",
    "IndexedFile",
    "content_hash",
    "describe",
    "enrich",
    "file_date",
    "imaging_available",
    "iter_media",
    "outline",
    "read_metadata",
    "scan_entries",
    "walk_follow",
)
