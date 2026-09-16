"""Bounded admission for owner-supplied images, behind opaque handles. WP1.2.

WHY THIS EXISTS WHEN `InputAsset` ARGUED AGAINST IT

`contracts.InputAsset` carries a reasoned objection to exactly this module:

    "Inline bytes rather than a handle into a new registry. A source image is
    used ONCE, by the job it arrives with, and dies with it. A registry would
    add a lifetime, an eviction policy and a second thing to leak."

That was right about the cost and wrong about the premise. It is true while a
generate call CONSUMES its payload -- which is what the Studio Extension does
(`studio_api.py:2882`, one `async def` returning the images inline, reviewed and
recorded in `Evidence/source-review/WP1.4-inpaint.md`). Studio queues: the
request is admitted, `202` is returned, and the job runs later. An image is no
longer used once by the call that carried it.

So the objection is answered rather than ignored. The lifetime is explicit
(`Retention`), the eviction policy is bounded and inherited from
`ResultRegistry`, and the "second thing to leak" is bounded by the same LRU that
bounds results.

INLINE STAYS. WP1.2 asks for bounded inline data "only as a normalization
compatibility path, then convert it to an asset record before job admission",
and that is what happens: the browser may keep sending a data URL, and it
becomes an asset record on the way in. Nothing about the page has to change for
the identity to exist.

NO PIL. This module stores bytes and answers questions about them. The DECODE
and every judgement needing real pixels stay in `forge_headless.input_assets`,
which is where `forge_studio`'s ban on PIL puts them. Hashing is `hashlib`,
which is stdlib and not what the ban is about.

NO PATHS. A handle is an opaque token. Nothing here holds a filesystem location
and nothing here can be persuaded to return one.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from secrets import token_hex
from typing import Any

from .contracts import StructuredError, StudioError

#: Mirrors `contracts.MAX_SOURCE_IMAGE_BYTES`. Checked against the ENCODED
#: string before any decode, because a decoded ceiling can only be applied
#: after spending the memory it exists to protect.
#: A TRANSPORT bound -- how much request body the server buffers for one
#: image -- and not a statement about how big a picture an owner may use.
#:
#: Raised from 32 MB on 2026-08-20. With `MAX_SOURCE_PIXELS` gone (AR6.3), this
#: became the new effective wall: an 8192x8192 PNG is around 100 MB before
#: base64, which is 4/3 again on the wire, so 32 MB refused the very images the
#: pixel-cap removal was meant to allow.
#:
#: Kept rather than removed, unlike the caps around it. This one bounds memory
#: the SERVER holds per request rather than anything the owner is making, and
#: an unbounded body is a way to exhaust it. 256 MB clears any real photograph
#: or canvas with room to spare.
MAX_ASSET_BYTES = 256 * 1024 * 1024

#: The formats Studio will admit. Narrow on purpose: each one is a decoder
#: reachable from an unauthenticated browser request.
ASSET_MEDIA_TYPES = ("image/png", "image/jpeg", "image/webp")

#: What an asset is FOR. Not decoration: a mask admitted as a source, or the
#: reverse, is a caller error worth naming rather than a shape to coerce.
ASSET_ROLES = ("source", "mask")

HANDLE_PREFIX = "studio-asset/"
HANDLE_PATTERN = re.compile(r"^studio-asset/[0-9a-f]{32}$")

#: `data:<media-type>;base64,<payload>`. Anchored -- a permissive match here is
#: a decoder reachable from an unvalidated browser string.
_DATA_URL = re.compile(
    r"^data:([a-z]+/[a-z0-9.+-]+);base64,(.+)$", re.IGNORECASE | re.DOTALL)

#: How many assets one process will hold. Bounded for the reason `InputAsset`
#: gave: an unbounded registry is a leak with extra steps.
MAX_ASSETS = 64


class Retention(str, Enum):
    """How long an admitted asset is allowed to live.

    `TRANSIENT` is the only class WP1 uses. It exists as an enum rather than a
    bool so the later classes arrive as a widening rather than a rewrite, and
    so a reader can see that the lifetime question was answered rather than
    deferred.
    """

    #: Dies with the job that referenced it. The Canvas source and mask.
    TRANSIENT = "transient"
    #: Survives until the process ends. Not used by WP1.
    SESSION = "session"
    #: Survives a restart. Requires the durable store; WP5 owns it.
    PERSISTENT = "persistent"


@dataclass(frozen=True)
class AdmittedAsset:
    """What the server established about one supplied image.

    Every field is a FACT computed here, unlike `InputAsset`, whose dimensions
    and byte length are the browser's claims. No data URL: a caller that wants
    the bytes asks for them by handle.
    """

    handle: str
    role: str
    media_type: str
    byte_length: int
    content_hash: str
    retention: Retention = Retention.TRANSIENT
    references: int = 0

    def to_dict(self) -> dict[str, Any]:
        """The browser projection. Deliberately not the bytes."""

        return {
            "handle": self.handle,
            "role": self.role,
            "media_type": self.media_type,
            "byte_length": self.byte_length,
            "content_hash": self.content_hash,
            "retention": self.retention.value,
        }


def _fail(code: str, message: str) -> StudioError:
    return StudioError(StructuredError(code=code, message=message))


class AssetService:
    """One bounded registry of admitted images, keyed by opaque handle."""

    def __init__(self, *, max_assets: int = MAX_ASSETS) -> None:
        self._lock = threading.RLock()
        self._entries: "OrderedDict[str, tuple[AdmittedAsset, bytes]]" = OrderedDict()
        self._evicted: set[str] = set()
        self._max_assets = max(1, int(max_assets))

    # -- admission ---------------------------------------------------------

    def admit(self, data_url: Any, *, role: str,
              retention: Retention = Retention.TRANSIENT) -> AdmittedAsset:
        """Take one inline image and return its handle, or refuse by name."""

        if role not in ASSET_ROLES:
            raise _fail("ASSET_ROLE_UNKNOWN",
                        f"{role!r} is not an asset role Studio admits.")
        if not isinstance(data_url, str) or not data_url:
            raise _fail("ASSET_MALFORMED", "That asset carries no image data.")
        if not data_url.startswith("data:"):
            # The boundary rule. A browser that can name a server file is a
            # browser that can read one, and no downstream check recovers from
            # having accepted the name.
            raise _fail("ASSET_MALFORMED",
                        "An asset must be inline image data, not a path or URL.")
        # BEFORE the decode, against the encoded string. The whole point.
        if len(data_url) > MAX_ASSET_BYTES:
            raise _fail(
                "ASSET_TOO_LARGE",
                f"That image is larger than the "
                f"{MAX_ASSET_BYTES // (1024 * 1024)} MB limit.")

        matched = _DATA_URL.match(data_url)
        if matched is None:
            raise _fail("ASSET_MALFORMED",
                        "That asset is not a base64 data URL.")
        media_type = matched.group(1).lower()
        if media_type not in ASSET_MEDIA_TYPES:
            raise _fail("ASSET_MEDIA_TYPE_UNSUPPORTED",
                        f"Studio does not read {media_type} images.")
        try:
            raw = base64.b64decode(matched.group(2), validate=True)
        except (binascii.Error, ValueError) as error:
            raise _fail("ASSET_MALFORMED",
                        "That asset is not valid base64.") from error
        if not raw:
            raise _fail("ASSET_MALFORMED", "That asset is empty.")

        asset = AdmittedAsset(
            handle=f"{HANDLE_PREFIX}{token_hex(16)}",
            role=role,
            media_type=media_type,
            byte_length=len(raw),
            content_hash=hashlib.sha256(raw).hexdigest(),
            retention=retention,
        )
        with self._lock:
            self._entries[asset.handle] = (asset, raw)
            self._entries.move_to_end(asset.handle)
            while len(self._entries) > self._max_assets:
                stale, _ = self._entries.popitem(last=False)
                # Remembered, so an evicted handle can be reported as EXPIRED
                # rather than as never having existed. The two are different
                # facts and an owner can act on the difference.
                self._evicted.add(stale)
        return asset

    # -- lookup ------------------------------------------------------------

    def describe(self, handle: Any, *, role: str | None = None) -> AdmittedAsset:
        """The record for one handle, or a named refusal.

        `role` is checked when supplied: an asset admitted as a mask must not
        satisfy a request for a source, because the two are verified against
        each other downstream and a swap would be discovered as a geometry
        failure rather than as the caller error it is.
        """

        entry = self._entry(handle)
        asset = entry[0]
        if role is not None and asset.role != role:
            raise _fail(
                "ASSET_ROLE_MISMATCH",
                f"That asset was admitted as a {asset.role}, not a {role}.")
        return asset

    def payload(self, handle: Any, *, role: str | None = None) -> bytes:
        """The stored bytes. The only way to get them, and never a path."""

        self.describe(handle, role=role)
        return self._entry(handle)[1]

    def _entry(self, handle: Any) -> tuple[AdmittedAsset, bytes]:
        if not isinstance(handle, str) or not HANDLE_PATTERN.match(handle):
            # A malformed handle is indistinguishable from an unknown one, and
            # is reported the same way so probing learns nothing.
            raise _fail("ASSET_NOT_FOUND", "That asset is not available.")
        with self._lock:
            found = self._entries.get(handle)
            if found is None:
                if handle in self._evicted:
                    raise _fail(
                        "ASSET_EXPIRED",
                        "That asset was released to make room for newer ones.")
                raise _fail("ASSET_NOT_FOUND", "That asset is not available.")
            self._entries.move_to_end(handle)
            return found

    # -- ownership ---------------------------------------------------------

    def retain(self, handle: str) -> AdmittedAsset:
        """Claim one reference. A retained asset is not evicted."""

        with self._lock:
            asset, raw = self._entry(handle)
            claimed = AdmittedAsset(
                handle=asset.handle, role=asset.role,
                media_type=asset.media_type, byte_length=asset.byte_length,
                content_hash=asset.content_hash, retention=asset.retention,
                references=asset.references + 1)
            self._entries[handle] = (claimed, raw)
            return claimed

    def release(self, handle: str) -> None:
        """Drop one reference, and the asset itself when the last one goes.

        Silent on an unknown handle: release runs on cleanup paths, including
        ones reached because something already failed, and a cleanup that
        raises is worse than a cleanup that finds nothing to do.
        """

        with self._lock:
            found = self._entries.get(handle)
            if found is None:
                return
            asset, raw = found
            remaining = asset.references - 1
            if remaining > 0:
                self._entries[handle] = (
                    AdmittedAsset(
                        handle=asset.handle, role=asset.role,
                        media_type=asset.media_type,
                        byte_length=asset.byte_length,
                        content_hash=asset.content_hash,
                        retention=asset.retention, references=remaining),
                    raw)
                return
            if asset.retention is Retention.TRANSIENT:
                del self._entries[handle]
                self._evicted.add(handle)

    def count(self) -> int:
        with self._lock:
            return len(self._entries)


__all__ = (
    "ASSET_MEDIA_TYPES",
    "ASSET_ROLES",
    "HANDLE_PREFIX",
    "MAX_ASSET_BYTES",
    "MAX_ASSETS",
    "AdmittedAsset",
    "AssetService",
    "Retention",
)
