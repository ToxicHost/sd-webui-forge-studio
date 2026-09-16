"""The Auto Detail detector catalogue: local files, named, never paths.

Studio owns this by owner decision, and it is the reason `get_models` was not
adapted from upstream. That function scans a directory AND reaches HuggingFace
AND injects four MediaPipe entries whether or not MediaPipe exists -- three
policies welded together, two of which this product refuses. Owning the
catalogue means the policy is one readable rule instead of an inherited one.

The rule, in full:

```text
local only        nothing is fetched, resolved, or discovered over a network,
                  at enumeration or at generation. A detector exists because a
                  file exists.
names only        a detector is identified by its NAME. No path reaches a
                  response, exactly as no model path does.
hash-checked      a BUNDLED detector whose bytes do not match the recorded
                  hash is REFUSED, not offered-and-then-failed. That is the
                  case where something is actually wrong.
own files welcome an unrecognised .pt is still offered, marked unverified.
                  The owner adds their own detectors; refusing them because
                  Studio did not ship them would be a different product.
```

Nothing here imports ultralytics, torch, or any detector runtime. Enumeration
reads file names and sizes and hashes bytes; it never loads a weight. That is
what lets the catalogue answer on a cold server, and it is why `/models`
listing cannot be the thing that drags a detector backend into a Studio
process.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

#: The extension upstream scans for, kept identical so a directory prepared for
#: ADetailer works here unchanged.
DETECTOR_SUFFIX = ".pt"

#: Bundled detectors, by name -> sha256. Recorded in
#: `docs/studio/BUNDLED_MODEL_ASSETS.md` with sizes, source and licence; this
#: is the machine-readable half of that record.
#:
#: From huggingface.co/Bingsu/adetailer at revision
#: 53cc19de382014514d9d4038601d261a7faa9b7b, licence Apache-2.0 -- which is a
#: SEPARATE obligation from the AGPL-3.0 of the adetailer code, and is recorded
#: separately so neither is assumed from the other.
BUNDLED_DETECTORS: dict[str, str] = {
    "face_yolov8n.pt":
        "70b640f8f60b1cf0dcc72f30caf3da9495eb2fb6509da48c53374ad6806e6a9c",
    "face_yolov8s.pt":
        "c7237eff25787377de196961140ceaed324d859ee8de5a775d93d33a0e3fab78",
    "hand_yolov8n.pt":
        "3991202eb69e9ddcb3b9ba80cdeb41e734ffaf844403d6c9f47d515cd88c6f29",
    "person_yolov8n-seg.pt":
        "38fc8aaae97cb6e70be4ec44770005b26ed473471362afcda62a0037d7ccf432",
    "person_yolov8s-seg.pt":
        "53c54aec2239355faffc6c5b70d0f3d05042f386f956cbec39cec46ad456f050",
}

#: Read in chunks: a detector is a few megabytes today, but the catalogue must
#: not decide how much memory it needs from how large a file someone dropped in.
_HASH_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Detector:
    """One offerable detector. Deliberately carries no path."""

    name: str
    #: Bytes on disk, so a caller can show something useful without a path.
    size: int
    #: True when this name is one Studio bundled AND the bytes match.
    verified: bool
    #: True when Studio has no recorded hash for this name -- an owner's own
    #: file. Offered, but the distinction is reported rather than hidden.
    owner_supplied: bool

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "size": self.size,
            "verified": self.verified,
            "owner_supplied": self.owner_supplied,
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _admitted(
    root: Path | str | None,
    known: Mapping[str, str],
) -> list[tuple[Detector, Path]]:
    """Every admitted detector under one root, WITH the file it came from.

    The single scan, extracted so there is exactly one admission rule.
    `scan_detectors` drops the paths; `resolve_detector_path` keeps them. The
    offered catalogue and the file a generation actually opens therefore
    cannot disagree -- which is the argument this module already makes for
    having one enumerator instead of two, applied one level down.
    """

    if root is None:
        return []
    base = Path(root)
    try:
        if not base.is_dir():
            return []
        candidates = sorted(
            p for p in base.rglob("*")
            if p.is_file() and p.suffix.lower() == DETECTOR_SUFFIX
        )
    except OSError:
        return []

    found: list[tuple[Detector, Path]] = []
    seen: set[str] = set()
    for path in candidates:
        name = path.name
        # First one wins. Two files of the same name in different subfolders
        # are one ambiguous choice, and picking silently by directory order
        # would make the answer depend on something the owner cannot see.
        if name in seen:
            continue
        seen.add(name)
        try:
            size = path.stat().st_size
        except OSError:
            continue
        expected = known.get(name)
        if expected is None:
            found.append(
                (Detector(name=name, size=size, verified=False,
                          owner_supplied=True), path)
            )
            continue
        try:
            actual = _sha256(path)
        except OSError:
            continue
        if actual != expected:
            # A name Studio ships, carrying bytes Studio did not. Refused
            # rather than offered-and-failed-later: this is corruption or
            # substitution, and it is the one case where silence would be
            # dangerous rather than merely unhelpful.
            continue
        found.append(
            (Detector(name=name, size=size, verified=True,
                      owner_supplied=False), path)
        )
    return found


def scan_detectors(
    root: Path | str | None,
    *,
    bundled: Mapping[str, str] | None = None,
) -> tuple[Detector, ...]:
    """Every offerable detector under `root`, in a stable order.

    An absent or unreadable root yields NOTHING rather than raising. A missing
    detector directory is an ordinary state -- the owner has not added one yet
    -- and it is reported by an empty catalogue, the same way an engine that is
    not standing reports empty registries instead of inventing a plausible
    list.

    Recursive, matching upstream's `rglob`, so a directory organised into
    subfolders works unchanged.
    """

    known = dict(BUNDLED_DETECTORS if bundled is None else bundled)
    return tuple(detector for detector, _ in _admitted(root, known))


def resolve_detector_path(
    name: str,
    registry: Any,
    *,
    bundled: Mapping[str, str] | None = None,
) -> Path | None:
    """The file an admitted NAME refers to, or None.

    The one place a name becomes a path, and the only one there may be.
    `detector_adapter.detect` requires a path and says so: "callers hold
    NAMES; the catalogue turns an admitted name into a path and nothing else
    may." This is that turn.

    It walks the same admission the offered catalogue walks, in the same
    order, so a name the owner was offered resolves, and a name that was
    refused -- unknown, hash-mismatched, unreadable -- resolves to None rather
    than being smuggled through as a path. An unresolvable name is not an
    error here; the caller refuses, because only the caller knows whether the
    slot asking was enabled.
    """

    wanted = (name or "").strip()
    if not wanted:
        return None
    known = dict(BUNDLED_DETECTORS if bundled is None else bundled)
    try:
        roots = registry.configured_roots().get("adetailer", ())
    except Exception:  # noqa: BLE001 - a missing catalogue resolves nothing
        return None

    seen: set[str] = set()
    for root in roots:
        for detector, path in _admitted(root, known):
            # Every name is claimed as it is passed, not only the wanted one.
            # Otherwise a second root holding the same name would answer for a
            # file the catalogue never offered -- first root wins, exactly as
            # `scan_configured_detectors` decides it.
            if detector.name in seen:
                continue
            seen.add(detector.name)
            if detector.name == wanted:
                return path
    return None


def scan_configured_detectors(registry: Any) -> tuple[Detector, ...]:
    """Every offerable detector under the CONFIGURED `adetailer` root(s).

    The bridge between the two things that both know about that directory,
    and the reason there is only one enumerator rather than two.

    `MODEL_ROLES` gained `adetailer` so the root is configurable, validated
    for containment and reparse points, and persisted with the other three.
    That machinery owns WHERE detectors live. This module owns WHICH of them
    may be offered -- hash-checked, names never paths, an owner's own file
    admitted but marked. Neither answer is complete without the other, and
    two independent directory scans would be free to disagree about the same
    folder while both believed themselves authoritative.

    A registry that does not know the role, or has no root for it, yields
    nothing. That is the ordinary state before the owner points at a folder,
    not an error.
    """

    try:
        roots = registry.configured_roots().get("adetailer", ())
    except Exception:  # noqa: BLE001 - a missing catalogue is an empty one
        return ()

    found: list[Detector] = []
    seen: set[str] = set()
    for root in roots:
        for detector in scan_detectors(root):
            # First root wins, matching the single-root rule inside
            # `scan_detectors`: two files of the same name in two roots are
            # one ambiguous choice, and resolving it by configuration order
            # would make the answer depend on something invisible.
            if detector.name in seen:
                continue
            seen.add(detector.name)
            found.append(detector)
    return tuple(found)


def describe_catalogue(detectors: Iterable[Detector]) -> dict[str, Any]:
    """The public projection. Names, sizes and flags -- never a path."""

    entries = [detector.describe() for detector in detectors]
    return {
        "detectors": entries,
        "count": len(entries),
        "verified_count": sum(1 for e in entries if e["verified"]),
    }


def is_known_detector(name: str, detectors: Iterable[Detector]) -> bool:
    """Whether a requested detector is one this catalogue would offer.

    The membership check that makes `hires.upscaler`'s lesson stick: three
    times now a chooser has been added whose `is_known_*` helper was written,
    exported, and never called, so an unknown value reached dispatch and died
    there -- after the base pass had been paid for. This one exists to be
    wired at the same time as the field it guards, not afterwards.
    """

    wanted = (name or "").strip()
    return any(wanted == detector.name for detector in detectors)


__all__ = (
    "BUNDLED_DETECTORS",
    "DETECTOR_SUFFIX",
    "Detector",
    "describe_catalogue",
    "is_known_detector",
    "resolve_detector_path",
    "scan_configured_detectors",
    "scan_detectors",
)
