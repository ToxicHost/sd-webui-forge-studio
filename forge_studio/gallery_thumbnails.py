"""Making the small version, and not making it twice.

The grid asks for hundreds of thumbnails as an owner scrolls. The Extension
renders each one on demand and relies on the browser's cache to stop it
happening again, which works until the page is hard-refreshed or opened in a
second tab -- and then a forty-thousand-image library re-decodes forty
thousand full-size PNGs.

So the bytes are kept, under the `thumbs/` directory `gallery_store` already
makes. The cache is keyed by the SAME identity the ETag uses -- path, size and
modification time -- which means invalidation is not a separate mechanism that
can disagree with the one the browser uses. Edit the file and its identity
changes; the old entry is simply never asked for again, and `sweep()` removes
what nothing has asked for.

TWO THINGS ARE PRESERVED THAT ARE EASY TO DROP:

* **orientation**. A photograph carries its rotation in EXIF rather than in its
  pixels, so a thumbnail made without `exif_transpose` is sideways while the
  full image is upright.
* **the colour profile**. Dropping the ICC profile makes every thumbnail from a
  wide-gamut source visibly duller than the image it stands for, which reads as
  the thumbnail being wrong rather than the pipeline being lossy.

VIDEO NEEDS AN EXTERNAL PROGRAM. Extracting a frame means ffmpeg, which is not
a Python dependency and may not be installed. Absent, a video gets a drawn
placeholder rather than a broken image -- the file is real and the owner should
see it in the grid, they just cannot have a frame from it.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .gallery_index import ImagingUnavailable, VIDEO_SUFFIXES

#: What the grid asks for. Large enough that a retina tile is still sharp.
THUMBNAIL_SIZE = 640

#: Bumped when the rendering changes, so every cached entry and every ETag the
#: browser holds is invalidated at once.
THUMBNAIL_VERSION = "1"

WEBP_QUALITY = 90

#: How long an entry may go unasked-for before `sweep` may remove it.
CACHE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60

#: Drawn, not fetched: a placeholder must not itself need the network.
VIDEO_PLACEHOLDER = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" '
    b'width="200" height="200"><rect width="200" height="200" fill="#16161c"/>'
    b'<polygon points="80,60 80,140 140,100" fill="#d4a017" opacity="0.7"/>'
    b'<rect x="40" y="155" width="120" height="20" rx="4" fill="#2a2a35"/>'
    b'<text x="100" y="169" text-anchor="middle" font-family="sans-serif" '
    b'font-size="11" fill="#8888a0">VIDEO</text></svg>'
)
VIDEO_PLACEHOLDER_TYPE = "image/svg+xml"


@dataclass(frozen=True)
class Thumbnail:
    """Bytes, and everything needed to serve them."""

    data: bytes
    media_type: str
    etag: str
    #: False for the video placeholder: a stand-in must not be cached for a
    #: week, or installing ffmpeg later would change nothing an owner can see.
    cacheable: bool = True


def ffmpeg_path() -> str | None:
    """Where ffmpeg is, or None. Looked up, never assumed."""

    return shutil.which("ffmpeg")


def identity(path: Path, size: int = THUMBNAIL_SIZE) -> str:
    """What makes this thumbnail this thumbnail.

    Modification time is in it, so editing the source produces a different
    identity and neither the disk cache nor the browser can serve the old
    picture. A file that cannot be stat-ed still gets an identity -- it may
    have been deleted between the query and the request, and a crash there
    would be a worse answer than a 404.
    """

    try:
        stamp = str(Path(path).stat().st_mtime_ns)
    except OSError:
        stamp = "0"
    return hashlib.sha256(
        f"{THUMBNAIL_VERSION}:{size}:{path}:{stamp}".encode(
            "utf-8", "surrogatepass"
        )
    ).hexdigest()


def render(path: str | Path, size: int = THUMBNAIL_SIZE) -> bytes | None:
    """The small version, as WEBP. None if the file cannot be read."""

    try:
        from PIL import Image, ImageOps
    except ImportError as error:
        raise ImagingUnavailable(
            "Thumbnails cannot be made because Pillow is not installed."
        ) from error

    from io import BytesIO

    try:
        with Image.open(path) as image:
            profile = image.info.get("icc_profile")
            # Rotation lives in EXIF, not in the pixels. Without this the
            # thumbnail is sideways while the full image is upright.
            image = ImageOps.exif_transpose(image)
            image.thumbnail((size, size), Image.LANCZOS)
            if image.mode in ("RGBA", "P", "LA"):
                image = image.convert("RGB")
            buffer = BytesIO()
            options: dict[str, Any] = {"quality": WEBP_QUALITY}
            if profile:
                # Kept, or every thumbnail from a wide-gamut source looks
                # duller than the image it stands for.
                options["icc_profile"] = profile
            image.save(buffer, "WEBP", **options)
            return buffer.getvalue()
    except (OSError, ValueError, TypeError):
        return None


def render_video_frame(path: str | Path, size: int = THUMBNAIL_SIZE
                       ) -> bytes | None:
    """One frame, via ffmpeg. None if ffmpeg is absent or the file is not one.

    Half a second in first: many clips open on a black or blank frame, so
    seeking a little way in gives a picture of what the clip is actually of.
    Falling back to the very start covers clips shorter than that.
    """

    program = ffmpeg_path()
    if program is None:
        return None
    for seek in ("0.5", "0", None):
        command = [program]
        if seek is not None:
            command += ["-ss", seek]
        command += [
            "-i", str(path),
            "-frames:v", "1",
            "-vf", f"scale={size}:{size}:force_original_aspect_ratio=decrease",
            "-f", "image2", "-c:v", "mjpeg", "-q:v", "5", "-y", "pipe:1",
        ]
        try:
            finished = subprocess.run(command, capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            continue
        if finished.returncode == 0 and len(finished.stdout) > 100:
            return finished.stdout
    return None


class ThumbnailCache:
    """Rendered thumbnails on disk, keyed by their identity."""

    def __init__(self, directory: str | Path,
                 size: int = THUMBNAIL_SIZE) -> None:
        self.directory = Path(directory)
        self.size = size

    def entry(self, key: str) -> Path:
        """Where an identity is stored.

        Two levels, because a single directory holding forty thousand files is
        slow to open on every filesystem that has an opinion about it.
        """

        return self.directory / key[:2] / f"{key}.webp"

    def get(self, path: str | Path) -> Thumbnail | None:
        """A previously rendered thumbnail, if there is one."""

        key = identity(Path(path), self.size)
        entry = self.entry(key)
        try:
            data = entry.read_bytes()
        except OSError:
            return None
        if not data:
            return None
        # Touched so `sweep` can tell what is still in use. Failure is fine:
        # the entry is still valid, it just looks older than it is.
        try:
            entry.touch()
        except OSError:
            pass
        return Thumbnail(data, "image/webp", key)

    def put(self, path: str | Path, data: bytes) -> str:
        key = identity(Path(path), self.size)
        entry = self.entry(key)
        try:
            entry.parent.mkdir(parents=True, exist_ok=True)
            # Written beside and renamed: a half-written thumbnail read by
            # another request would be a corrupt image the browser then caches.
            temporary = entry.with_suffix(f".{id(data):x}.part")
            temporary.write_bytes(data)
            temporary.replace(entry)
        except OSError:
            pass  # a cache that cannot write is slow, not broken
        return key

    def thumbnail_for(self, path: str | Path) -> Thumbnail | None:
        """The whole job: cached if possible, rendered if not.

        Returns None only when the file genuinely cannot be turned into a
        picture -- the caller decides whether that is a 404 or a fallback to
        serving the original.
        """

        path = Path(path)
        if path.suffix.lower() in VIDEO_SUFFIXES:
            return self._video(path)

        found = self.get(path)
        if found is not None:
            return found
        data = render(path, self.size)
        if data is None:
            return None
        return Thumbnail(data, "image/webp", self.put(path, data))

    def _video(self, path: Path) -> Thumbnail:
        found = self.get(path)
        if found is not None:
            return found
        frame = render_video_frame(path, self.size)
        if frame is not None:
            key = identity(path, self.size)
            self.entry(key).parent.mkdir(parents=True, exist_ok=True)
            try:
                self.entry(key).write_bytes(frame)
            except OSError:
                pass
            return Thumbnail(frame, "image/jpeg", key)
        # Not cacheable: installing ffmpeg later must actually change what the
        # owner sees, rather than being masked by a week-old placeholder.
        return Thumbnail(
            VIDEO_PLACEHOLDER, VIDEO_PLACEHOLDER_TYPE,
            identity(path, self.size), cacheable=False,
        )

    def sweep(self, max_age: float = CACHE_MAX_AGE_SECONDS) -> int:
        """Remove entries nothing has asked for in a long time.

        Derived data, so losing an entry costs one re-render. The cache would
        otherwise grow by one file for every image the owner ever edits.
        """

        if not self.directory.is_dir():
            return 0
        cutoff = time.time() - max_age
        removed = 0
        for entry in self.directory.rglob("*.webp"):
            try:
                if entry.stat().st_mtime < cutoff:
                    entry.unlink()
                    removed += 1
            except OSError:
                continue
        return removed

    def clear(self) -> int:
        """Throw the whole cache away."""

        if not self.directory.is_dir():
            return 0
        removed = 0
        for entry in self.directory.rglob("*.webp"):
            try:
                entry.unlink()
                removed += 1
            except OSError:
                continue
        return removed


__all__ = (
    "CACHE_MAX_AGE_SECONDS",
    "THUMBNAIL_SIZE",
    "THUMBNAIL_VERSION",
    "VIDEO_PLACEHOLDER",
    "VIDEO_PLACEHOLDER_TYPE",
    "Thumbnail",
    "ThumbnailCache",
    "ffmpeg_path",
    "identity",
    "render",
    "render_video_frame",
)
