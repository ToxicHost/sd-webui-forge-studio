"""What a path IS, decided once for the whole product.

Containment, case behaviour, redirection and name acceptability were private to
`catalogue.py` while the model scan was the only thing that needed them. P0.4
adds a second consumer -- the directory browser -- and two implementations of
"is this path inside that one" is how the two drift until one of them is wrong
in a way nobody notices.

So they live here, lifted with their reasoning intact, and `catalogue.py`
imports them under its old private names. Nothing about the model scan changes.

Three fixes ride along, each one a real cross-platform defect rather than a
tidy-up:

```text
detect_case_policy_readonly   one odd entry could decide the whole verdict
name_is_acceptable            measured characters where the limits are bytes
                              and UTF-16 code units
nfc                           the same file can present decomposed from a
                              scan and composed from a typed path
```

This module reads `os.name`, which selects between real API differences. It
does not read `sys.platform` or `platform.system`, which would be guessing at
a platform's identity rather than asking what it can do.
"""

from __future__ import annotations

import os
import stat
import unicodedata
from pathlib import Path

# The one CasePolicy, imported rather than redeclared. A second enum with the
# same member names would compare unequal to this one and carries no
# AUTO_DETECT, so the two would agree in every test and disagree in the product
# -- which is the drift this module exists to remove, reintroduced by the
# module removing it.
from forge_studio.result_delivery import CasePolicy

#: Longest directory-entry name the catalogue will accept, in EVERY unit it
#: could be measured in. See `name_is_acceptable`.
MAX_NAME_LENGTH = 255

#: How many case-flippable entries the case probe will examine before giving
#: up. Bounded because the probe runs on an owner-named directory that may hold
#: hundreds of thousands of files, and because a verdict that needs more than a
#: handful of samples is not a verdict.
MAX_CASE_PROBE_CANDIDATES = 64


def nfc(text: str) -> str:
    """Normalize to NFC for identity comparison and digesting.

    macOS is the reason. HFS+ stores decomposed names, and APFS preserves what
    it was given while comparing normalization-insensitively -- so one file can
    arrive as NFD from a directory scan and as NFC from a path the owner typed,
    and a digest over the raw string would mint two different ids for it.

    Applied to identity only. The name shown to the owner and the name used to
    open the file stay exactly as the filesystem gave them, because normalizing
    a path before opening it can name a different file, or none.
    """

    return unicodedata.normalize("NFC", text)


def name_is_acceptable(name: str) -> bool:
    """Whether a directory-entry name is short enough to catalogue.

    Measured in all three units the limit could mean, because the platforms
    disagree about which one counts:

    ```text
    characters        what len() returns, and what this used to check
    UTF-8 bytes       what Linux and most POSIX filesystems limit
    UTF-16 code units what Windows and NTFS limit
    ```

    A 200-character name of astral-plane emoji is 800 UTF-8 bytes and 400
    UTF-16 code units. Checking characters alone accepted it on every platform
    and then met the real limit somewhere further down. Failing closed on the
    strictest reading is the same trade the rest of this module makes.

    Extracted so the policy is testable directly: Windows cannot create a name
    long enough to exercise it through the filesystem, and a test that skips
    proves nothing.
    """

    if len(name) > MAX_NAME_LENGTH:
        return False
    if len(name.encode("utf-8", "surrogatepass")) > MAX_NAME_LENGTH:
        return False
    # //2 because UTF-16-LE is two bytes per code unit; surrogate pairs
    # correctly count as the two units the filesystem also counts them as.
    if len(name.encode("utf-16-le", "surrogatepass")) // 2 > MAX_NAME_LENGTH:
        return False
    return True


def is_redirecting(entry: os.DirEntry[str]) -> bool:
    """Whether a directory entry redirects somewhere else.

    ``DirEntry.is_symlink()`` alone is not enough on Windows. CPython only
    reports ``S_IFLNK`` for ``IO_REPARSE_TAG_SYMLINK``, so a **directory
    junction** -- created by ``mklink /J``, which needs no administrator rights
    -- reports ``is_symlink() == False`` and ``is_dir(follow_symlinks=False) ==
    True``. A recursive scan would then walk straight through it and out of the
    root.

    Testing ``FILE_ATTRIBUTE_REPARSE_POINT`` catches every reparse tag, not the
    two that happen to have helper functions. Entries that cannot be classified
    are treated as redirecting, because "could not tell" must not mean "allow".

    Cost worth naming: cloud-placeholder files (OneDrive and similar) are also
    reparse points, so models stored behind one will not be listed. That is the
    same trade already made for symlinks, and it fails in the safe direction.
    """
    try:
        if entry.is_symlink():
            return True
    except OSError:
        return True
    if os.name != "nt":
        return False
    try:
        attributes = entry.stat(follow_symlinks=False).st_file_attributes
    except (OSError, AttributeError):
        return True
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def has_entries(root: Path) -> bool:
    """Whether the root holds anything at all.

    Used only to exempt an empty root from the inconclusive-policy refusal. A
    root that cannot be listed counts as non-empty, so an unreadable directory
    is refused rather than waved through as "empty".
    """
    try:
        with os.scandir(root) as entries:
            return next(iter(entries), None) is not None
    except OSError:
        return True


def _identity(path: Path) -> tuple[int, int] | None:
    """``(st_dev, st_ino)`` from an ``lstat``, or None if it cannot be had.

    ``lstat``, not ``stat``: the question is whether these two NAMES reach the
    same directory entry, and following a link would answer a different one.
    A broken link has an identity; its target does not.
    """
    try:
        info = os.lstat(path)
    except OSError:
        return None
    return (info.st_dev, info.st_ino)


def detect_case_policy_readonly(root: Path) -> CasePolicy:
    """Decide case behaviour without writing anything into ``root``.

    ``forge_studio.result_delivery.detect_case_policy`` creates a probe file,
    which is fine for a root Studio owns but wrong for a model directory that
    may be read-only or shared. This variant re-uses an existing entry instead
    and returns ``INCONCLUSIVE`` when it cannot decide.

    ``INCONCLUSIVE`` is not treated as "probably fine". An earlier version of
    this docstring argued it was safe because containment tries an exact prefix
    match first, which needs no policy -- that was wrong on Windows, where
    ``pathlib`` compared case-insensitively and the "exact" branch quietly
    accepted case-differing paths. ``is_prefix`` no longer delegates to
    ``pathlib`` for that branch, and a non-empty root whose case behaviour is
    inconclusive is refused outright.

    **What P0.4 corrected.** The probe used to decide from the FIRST entry
    whose name changes under ``swapcase``, using ``Path.exists`` and
    ``os.path.samefile`` -- both of which follow links. One awkward entry could
    therefore settle the verdict for the whole directory:

    ```text
    a broken symlink       exists() is False on a case-INSENSITIVE volume,
                           so the probe concluded CASE_SENSITIVE
    an unreadable entry    samefile raised, so the probe concluded
                           INCONCLUSIVE -- and a non-empty root with an
                           inconclusive policy is REFUSED, so one bad entry
                           made the whole model folder unusable
    ```

    Now it asks ``lexists`` and compares ``lstat`` identity, so links are
    answered rather than followed, and an ambiguous candidate is SKIPPED rather
    than allowed to decide. Fail-closed behaviour is unchanged in the direction
    that matters: running out of candidates still yields ``INCONCLUSIVE``, and
    ``INCONCLUSIVE`` still refuses. The fix removes a false verdict, not the
    refusal.
    """

    try:
        names = os.listdir(root)
    except OSError:
        return CasePolicy.INCONCLUSIVE

    candidates = 0
    for name in names:
        if candidates >= MAX_CASE_PROBE_CANDIDATES:
            break
        flipped = name.swapcase()
        if flipped == name:
            # Digits, punctuation, CJK, emoji: carries no case, so it can say
            # nothing about how the volume treats case.
            continue
        candidates += 1

        original = root / name
        variant = root / flipped
        if not os.path.lexists(variant):
            # The flipped spelling names nothing. Only a case-sensitive volume
            # can be in that state, and `lexists` answered without following a
            # link, so a broken link cannot fake it -- which `exists` could,
            # and did.
            return CasePolicy.CASE_SENSITIVE

        here = _identity(original)
        there = _identity(variant)
        if here is None or there is None:
            # Could not read one of them. Say nothing and look at the next
            # candidate: a single unreadable entry must not decide the policy
            # for the whole directory.
            continue
        if here == there:
            return CasePolicy.CASE_INSENSITIVE
        # Two distinct entries whose names differ only by case: the volume is
        # case-sensitive and both really exist.
        return CasePolicy.CASE_SENSITIVE

    return CasePolicy.INCONCLUSIVE


def is_prefix(
    root: Path,
    candidate: Path,
    policy: CasePolicy,
    *,
    allow_equal: bool = False,
) -> bool:
    """Prefix containment under an explicit case policy, never under os.name.

    Both branches compare path *components*, so ``C:\\Models`` never contains
    ``C:\\Models-Evil``; only the casefolding differs between them.

    The sensitive branch deliberately does not use ``Path.relative_to``. On
    Windows, ``pathlib`` compares through the Windows flavour, which lowercases:
    ``Path('c:/models/x').relative_to(Path('C:/Models'))`` succeeds on CPython
    3.13. Using it here would have made the "case-sensitive" branch silently
    case-insensitive on the platform this runs on, which in turn would have made
    an INCONCLUSIVE policy behave as though it had concluded "insensitive".
    Comparing raw components keeps the policy the only thing that decides.
    """
    root_parts = root.parts
    candidate_parts = candidate.parts
    if len(candidate_parts) < len(root_parts):
        return False
    deep_enough = allow_equal or len(candidate_parts) > len(root_parts)

    # Exact components. Needs no filesystem access and no case rules at all, and
    # is the branch that almost always decides: resolve() returns canonical
    # on-disk casing on Windows, so both sides already agree.
    if candidate_parts[: len(root_parts)] == root_parts:
        return deep_enough
    if policy is not CasePolicy.CASE_INSENSITIVE:
        return False

    # Case-insensitive volume, and the spellings differ. Ask the filesystem
    # whether the two directories are the same one, rather than emulating its
    # case rules.
    #
    # This replaced a casefold() comparison, which was a real containment hole:
    # str.casefold() performs FULL case folding, so 'Modelſ'.casefold() ==
    # 'models' and 'gro\xdfe'.casefold() == 'grosse'. NTFS folds with a simple
    # uppercase table and does neither, so those directories genuinely coexist
    # on disk while casefold declared one to contain the other. An attacker who
    # could write into the root could then plant a junction into the twin
    # directory and have its contents catalogued as contained.
    ancestor = Path(*candidate_parts[: len(root_parts)])
    try:
        if not os.path.samefile(ancestor, root):
            return False
    except OSError:
        # Cannot ask, so cannot claim containment.
        return False
    return deep_enough


def canonical_spelling(resolved: Path) -> Path:
    """The on-disk spelling of an already-resolved directory.

    NOT WIRED. Ships tested so the decision to use it is a one-line change
    rather than a design exercise.

    Windows `resolve()` already returns canonical casing. POSIX does not: it
    returns the spelling it was given, so `/Users/me/Models` and
    `/Users/me/models` on a case-insensitive APFS volume are one directory
    under two spellings -- and a digest keyed on the spelling mints two
    different ids for every model in it.

    Turning this on would change `_root_key` once for affected POSIX owners and
    silently invalidate their remembered selection, so it is a separate
    decision from the browser and is taken separately.
    """

    parts = list(resolved.parts)
    if not parts:
        return resolved
    walked = Path(parts[0])
    for part in parts[1:]:
        try:
            with os.scandir(walked) as entries:
                match = next(
                    (e.name for e in entries if _same_name(e.name, part)), None
                )
        except OSError:
            return resolved
        if match is None:
            return resolved
        walked = walked / match
    return walked


def _same_name(candidate: str, wanted: str) -> bool:
    """Whether two directory-entry spellings name the same entry.

    Compared under NFC so a decomposed on-disk name matches a composed typed
    one, then case-insensitively, because this is only ever asked while
    canonicalising a spelling that already resolved.
    """

    left = nfc(candidate)
    right = nfc(wanted)
    return left == right or left.lower() == right.lower()


__all__ = (
    "MAX_CASE_PROBE_CANDIDATES",
    "MAX_NAME_LENGTH",
    "CasePolicy",
    "canonical_spelling",
    "detect_case_policy_readonly",
    "has_entries",
    "is_prefix",
    "is_redirecting",
    "name_is_acceptable",
    "nfc",
)
