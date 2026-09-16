"""What this Studio's Gallery can actually do, and why not.

D8's rule, applied to a feature with three separate dependencies:

```text
absent                   -> HIDE
present but unavailable  -> DISABLE WITH REASON
```

The Gallery service is present in this build, so it is never hidden. What
varies is whether it can run, and that question has more than one answer:
browsing needs Pillow, and the auto-sync watcher needs a filesystem-notification
library. The second is optional, and a Gallery that silently omits a page on a
machine that lacks it teaches its owner that the feature is broken.

Duplicate detection USED to be a third answer, gated on `imagehash` and its
scipy/PyWavelets tail. `gallery_similarity` computes the same hash itself, so
it now needs nothing beyond an image Studio can already decode.

So each sub-feature reports itself, with a reason in the owner's terms.

WHAT THIS MODULE REFUSES TO DO is as much the point as what it reports. The
Extension's Gallery reaches for pip at import time:

```python
except ImportError:
    print("[Gallery] watchdog not found - installing...")
    _sp.check_call([sys.executable, "-m", "pip", "install", "watchdog", ...])
```

Studio does not install things. That line reaches the network during startup,
mutates the environment the owner assembled, can leave a half-installed package
behind on a timeout, and turns a missing optional feature into a failed launch.
A missing dependency here becomes a sentence an owner can read and act on, and
the decision of whether to install stays theirs.

Every fact is injectable, so the answer this module would give on a machine
without Pillow is testable on a machine with it.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from typing import Any

#: The name an owner would have to install, per feature. Reported so the
#: sentence is actionable rather than merely apologetic.
PILLOW_PACKAGE = "Pillow"
WATCHDOG_PACKAGE = "watchdog"


@dataclass(frozen=True)
class FeatureState:
    """One capability, and how it was decided."""

    available: bool
    reason: str | None = None
    #: What the owner would install to get it. Present only when absent, and
    #: never rendered as a command: Studio reports, the owner decides.
    requires: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"available": self.available}
        if self.reason:
            payload["reason"] = self.reason
        if self.requires:
            payload["requires"] = self.requires
        return payload


@dataclass(frozen=True)
class GalleryProbe:
    """The facts a decision is made from. Injectable on purpose.

    A capability that reads the real environment inside itself can only be
    tested on a machine that already has the shape you want to test.
    """

    imaging: bool = False
    perceptual_hashing: bool = False
    filesystem_watching: bool = False
    #: Whether the state root can be written. False on a read-only mount or a
    #: revoked permission, and the Gallery cannot keep an index without it.
    storage_writable: bool = True
    storage_reason: str | None = None

    @classmethod
    def measure(cls, *, storage_writable: bool = True,
                storage_reason: str | None = None) -> "GalleryProbe":
        """Read the real environment.

        `find_spec` rather than `import`: asking whether a package COULD be
        imported must not import it. Pulling `watchdog` in at capability time
        starts its own threads, and pulling `imagehash` in drags NumPy and
        SciPy into a process that may never open the Gallery.
        """

        def installed(name: str) -> bool:
            try:
                return importlib.util.find_spec(name) is not None
            except (ImportError, ValueError):
                return False

        imaging = installed("PIL")
        return cls(
            imaging=imaging,
            # Studio computes its own perceptual hash, so this needs nothing
            # beyond an image it can decode. It used to ask for `imagehash`,
            # which drags in scipy and PyWavelets and was the Gallery's one
            # genuinely blocked feature; TrackImage 3.94 had already dropped
            # that dependency and `gallery_similarity` goes one further by
            # needing no numeric library at all.
            perceptual_hashing=imaging,
            filesystem_watching=installed("watchdog"),
            storage_writable=storage_writable,
            storage_reason=storage_reason,
        )


@dataclass(frozen=True)
class GalleryCapability:
    """Whether the Gallery runs, and what it can do when it does."""

    available: bool
    reason: str | None = None
    features: dict[str, FeatureState] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"available": self.available}
        if self.reason:
            payload["reason"] = self.reason
        payload["features"] = {
            name: state.to_dict() for name, state in self.features.items()
        }
        return payload

    def feature(self, name: str) -> FeatureState:
        """A named sub-feature. An unknown name is unavailable, not an error:
        a page asking about something this build does not have should render a
        disabled control, not raise."""

        return self.features.get(
            name, FeatureState(False, f"{name} is not a Gallery feature.")
        )


def decide(probe: GalleryProbe) -> GalleryCapability:
    """Turn the probe's facts into what the page should render."""

    browsing = _browsing(probe)
    features = {
        "browse": browsing,
        "duplicates": _duplicates(probe, browsing),
        "auto_sync": _auto_sync(probe, browsing),
    }
    return GalleryCapability(
        available=browsing.available,
        reason=browsing.reason,
        features=features,
    )


def _browsing(probe: GalleryProbe) -> FeatureState:
    """The Gallery itself. Everything else depends on this one."""

    if not probe.storage_writable:
        return FeatureState(
            False,
            probe.storage_reason
            or "The Gallery cannot write its index to the Studio state folder.",
        )
    if not probe.imaging:
        return FeatureState(
            False,
            "The Gallery cannot read images because Pillow is not installed.",
            requires=PILLOW_PACKAGE,
        )
    return FeatureState(True)


def _duplicates(probe: GalleryProbe, browsing: FeatureState) -> FeatureState:
    if not browsing.available:
        return FeatureState(False, browsing.reason, browsing.requires)
    if not probe.perceptual_hashing:
        return FeatureState(
            False,
            "Duplicate detection needs to be able to read images. The rest "
            "of the Gallery works without it.",
            requires=PILLOW_PACKAGE,
        )
    return FeatureState(True)


def _auto_sync(probe: GalleryProbe, browsing: FeatureState) -> FeatureState:
    if not browsing.available:
        return FeatureState(False, browsing.reason, browsing.requires)
    if not probe.filesystem_watching:
        return FeatureState(
            False,
            "Watching folders for new files needs a filesystem-notification "
            "library, which is not installed. Rescan by hand to pick up new "
            "images.",
            requires=WATCHDOG_PACKAGE,
        )
    return FeatureState(True)


__all__ = (
    "PILLOW_PACKAGE",
    "WATCHDOG_PACKAGE",
    "FeatureState",
    "GalleryCapability",
    "GalleryProbe",
    "decide",
)
