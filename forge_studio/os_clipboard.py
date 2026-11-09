"""NM2. Put saved pictures on the computer's clipboard as FILES, with their names.

A web page can put a picture's pixels on the clipboard but never a file, so a
pasted picture had no name of its own (Discord calls it "image.png") and
Windows Explorer could not paste it at all. Studio's server runs on the
owner's computer, so it can do what Explorer's own Copy does: offer the saved
file itself (CF_HDROP, "copy" not "move"), plus the first picture's pixels
(PNG and CF_DIB) for apps that paste an image rather than a file.

Windows only. Elsewhere `copy_files` answers False and the page's own copy
(pixels only) stands. The caller decides that the browser is on this
computer: a remote browser's clipboard is on another machine.

`clipboard_formats` builds the bytes and touches nothing; `copy_files` hands
them to the OS through `writer`, which tests replace. No window is shown: the
clipboard owner is a message-only window that exists for the one call.
"""

from __future__ import annotations

import io
import struct
from pathlib import Path
from typing import Callable, Iterable

#: Windows' standard clipboard format numbers.
CF_DIB = 8
CF_HDROP = 15
#: Registered by name; the Windows writer resolves these.
PNG = "PNG"
PREFERRED_DROP_EFFECT = "Preferred DropEffect"
#: DROPEFFECT_COPY: a paste copies the file, never moves it.
DROPEFFECT_COPY = 1


def drop_files_bytes(paths: Iterable[Path]) -> bytes:
    """A DROPFILES block: a 20-byte header (file list at offset 20, wide
    characters), then each absolute path in UTF-16, each ending in a null, and
    one more null to end the list."""

    names = "".join(str(Path(p).resolve()) + "\0" for p in paths) + "\0"
    header = struct.pack("<IiiII", 20, 0, 0, 0, 1)
    return header + names.encode("utf-16-le")


def picture_bytes(path: Path) -> tuple[bytes, bytes] | None:
    """The picture as (PNG, DIB) bytes, or None when it can't be read."""

    try:
        from PIL import Image

        with Image.open(path) as image:
            image.load()
            rgb = image.convert("RGBA" if "A" in image.getbands() else "RGB")
        png = io.BytesIO()
        rgb.save(png, "PNG")
        bmp = io.BytesIO()
        rgb.save(bmp, "BMP")
    except Exception:  # noqa: BLE001 - the files still copy without pixels
        return None
    # CF_DIB is a BMP without its 14-byte file header.
    return png.getvalue(), bmp.getvalue()[14:]


def clipboard_formats(paths: list[Path]) -> dict[int | str, bytes]:
    """Every format to offer, by Windows format number or registered name."""

    formats: dict[int | str, bytes] = {
        CF_HDROP: drop_files_bytes(paths),
        PREFERRED_DROP_EFFECT: struct.pack("<I", DROPEFFECT_COPY),
    }
    pixels = picture_bytes(paths[0]) if len(paths) == 1 else None
    if pixels is not None:
        formats[PNG], formats[CF_DIB] = pixels
    return formats


def copy_files(paths: Iterable[Path | str], *,
               writer: Callable[[dict[int | str, bytes]], bool] | None = None) -> bool:
    """Offer these files on the clipboard. False when there is nothing to
    copy, this is not Windows, or the clipboard could not be opened."""

    files = [Path(p) for p in paths if p and Path(p).is_file()]
    if not files:
        return False
    if writer is None:
        # By capability, not by platform name: only Windows' ctypes has
        # WinDLL, which is what the writer needs.
        import ctypes

        if getattr(ctypes, "WinDLL", None) is None:
            return False
        writer = _windows_write
    return bool(writer(clipboard_formats(files)))


def _windows_write(formats: dict[int | str, bytes]) -> bool:  # pragma: no cover - needs a desktop
    import ctypes
    import time
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.RegisterClipboardFormatW.restype = wintypes.UINT
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    GMEM_MOVEABLE = 0x0002
    HWND_MESSAGE = wintypes.HWND(-3)

    # A message-only window owns the clipboard for this call: with no owner,
    # EmptyClipboard leaves SetClipboardData nothing to attach to. Never shown.
    window = user32.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 0, 0, HWND_MESSAGE, None, None, None)
    if not window:
        return False
    try:
        for _ in range(10):   # another program may hold it for a moment
            if user32.OpenClipboard(window):
                break
            time.sleep(0.05)
        else:
            return False
        try:
            user32.EmptyClipboard()
            files_written = False
            for key, data in formats.items():
                number = key if isinstance(key, int) else user32.RegisterClipboardFormatW(key)
                if not number:
                    continue
                handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
                if not handle:
                    continue
                target = kernel32.GlobalLock(handle)
                ctypes.memmove(target, data, len(data))
                kernel32.GlobalUnlock(handle)
                if user32.SetClipboardData(number, handle):
                    # The clipboard owns the memory now.
                    files_written = files_written or number == CF_HDROP
                else:
                    kernel32.GlobalFree(handle)
            return files_written
        finally:
            user32.CloseClipboard()
    finally:
        user32.DestroyWindow(window)
