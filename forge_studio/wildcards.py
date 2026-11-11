"""Wildcards: the files behind `__name__`, and what a prompt becomes.

A wildcard is a text file. `__character__` in a prompt means "pick one line
from character.txt". The file lives under a folder the owner chooses, and
nesting works both ways round -- a folder becomes part of the name
(`people/knight.txt` is `__people/knight__`), and a chosen line may itself
contain another wildcard.

WHY THIS EXISTS AT ALL. The standalone had no wildcard service. Every route
the page calls answered a truthful refusal: `/studio/wildcards` returned `[]`,
and `dynamic_prompts/config` reported `folder_mode: "disabled"` so the Browse
and Reset controls disabled themselves through wiring that already existed.
That was honest but it is not a Gallery of one; an owner asking for a wildcard
folder is asking for the feature.

THREE PROPERTIES ARE LOAD-BEARING.

**A name is a name, never a path.** `__../../etc/passwd__` resolves to nothing.
Every lookup goes through a dictionary built by walking the owner's folder, so
a prompt cannot reach a file the walk did not find -- and the walk stays inside
the chosen root.

**Expansion is reproducible.** The same prompt and the same seed give the same
picture, which is the whole contract of a seed. One `random.Random(seed)` drives
every choice in one expansion, and the choices are recorded so the infotext can
say what was picked. `gallery_metadata` already reads that line back out of a
PNG as `studio_dynamic_prompts`.

**Recursion terminates.** A wildcard file whose line names the wildcard it came
from would otherwise expand forever. Depth is bounded and the bound is reported
rather than silently truncating.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

#: `__name__`, where the name may carry `/` for a nested folder. Deliberately
#: narrow: letters, digits, dash, underscore, dot and slash. A prompt is not a
#: place to accept arbitrary text as a filename.
TOKEN = re.compile(r"__([A-Za-z0-9_./ \-]+?)__")

#: One innermost `{...}` group. `[^{}]*` cannot match a brace, so nesting
#: resolves inside-out across passes rather than needing a real parser.
BRACE = re.compile(r"\{([^{}]*)\}")

#: The `N$$` prefix of a multi-select. `\s*` on both sides of the digits, so
#: `{ 2 $$a|b|c}` parses -- an owner who spaces it out is not wrong.
MULTI = re.compile(r"^\s*(\d+)\s*\$\$(.*)$", re.DOTALL)

#: DP1. The `W::` in front of one option of a group: its share of the draw.
#: `{3::cat|dog}` is cat three times in four. A decimal is allowed, and spaces
#: around the number and after the colons, like every space around an option.
WEIGHT = re.compile(r"^\s*(\d+(?:\.\d*)?|\.\d+)\s*::(.*)$", re.DOTALL)

#: DP1. A weight larger than this is this. Keeps the sum a number.
MAX_WEIGHT = 1e9

#: DP1. Stands where a drawn "nothing" was until the prompt is settled, so the
#: comma it would leave behind can be found. Never in a returned prompt.
_NOTHING = "\x00"
_N = re.escape(_NOTHING)
_NOTHING_RUN = re.compile(_N + r"(?:\s*" + _N + r")+")
_NOTHING_BETWEEN = re.compile(r",\s*" + _N + r"\s*(?=,)")
_NOTHING_FIRST = re.compile(r"^\s*" + _N + r"\s*(?:,\s*)?")
_NOTHING_LAST = re.compile(r"\s*(?:,\s*)?" + _N + r"\s*$")
_NOTHING_INSIDE = re.compile(r"(?<=\S)[^\S\n]+" + _N + r"[^\S\n]+(?=\S)")

#: What a wildcard file is.
WILDCARD_SUFFIX = ".txt"

#: How deep a wildcard may resolve into another before Studio stops.
MAX_DEPTH = 12

#: A file bigger than this is not a word list. Reading a multi-gigabyte file
#: into memory because it happened to end in .txt is not a service.
MAX_FILE_BYTES = 8 * 1024 * 1024

#: Ceiling on how many files one folder may contribute.
MAX_WILDCARDS = 20_000


@dataclass(frozen=True)
class Wildcard:
    """One file, as the browser lists it."""

    name: str
    path: Path
    size: int

    def to_dict(self) -> dict[str, Any]:
        # `kb` is what `wildcard-browser.js` reads and renders.
        return {"name": self.name, "kb": round(self.size / 1024.0, 1)}


@dataclass
class Expansion:
    """What one prompt became, and what was chosen along the way."""

    text: str
    choices: list[tuple[str, str]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    truncated: bool = False
    #: Things the owner should be told about their own prompt: a malformed
    #: multi-select count, a choice group that was empty after stripping. NOT
    #: for missing wildcards -- those have their own list, and reporting them
    #: twice in two vocabularies helps nobody.
    warnings: list[str] = field(default_factory=list)
    #: Whether the pass just run replaced anything. Loop bookkeeping, reset by
    #: `expand` between passes and never meaningful to a reader of the result.
    substituted: bool = False

    @property
    def summary(self) -> str:
        """The `Studio dynamic prompts:` line, for the infotext.

        `name=value` pairs, comma separated -- the shape `gallery_metadata`
        already parses back out of a PNG. Without it an owner can see the
        resolved prompt but never which wildcard produced which word.
        """

        return ", ".join(f"{name}={value}" for name, value in self.choices)

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.text,
            "choices": [{"name": n, "value": v} for n, v in self.choices],
            "missing": list(self.missing),
            "truncated": self.truncated,
            "warnings": list(self.warnings),
            # `wildcard-browser.js:642` and `tag-complete.js:724` both read
            # `expanded`. They are byte-identical to the Extension's, which
            # emits that key, so the preview panel rendered blank numbered
            # rows against `prompt` alone. Both are sent rather than renaming,
            # because `prompt` is what this tree's own tests already read.
            "expanded": self.text,
        }


def _settle(text: str) -> str:
    """The prompt as it would have been typed without the group. DP1.

    A drawn "nothing" takes one comma with it: `1boy, <nothing>, smile` is
    `1boy, smile`, at the start it takes the comma after it and at the end the
    one before. Between words it leaves one space. Only ever run on a text
    that drew a nothing, so every other prompt is returned byte for byte.
    """

    text = _NOTHING_RUN.sub(_NOTHING, text)
    text = _NOTHING_BETWEEN.sub("", text)
    text = _NOTHING_FIRST.sub("", text)
    text = _NOTHING_LAST.sub("", text)
    text = _NOTHING_INSIDE.sub(" ", text)
    return text.replace(_NOTHING, "")


def _options(body: str) -> tuple[list[str], list[float], bool]:
    """A group's options, their weights, and whether any weight was written.

    An option is what stands between two bars, spaces around it ignored. A
    blank one is a choice like any other: it means nothing is written. An
    option with no `W::` weighs 1, as in the Dynamic Prompts syntax guide
    ("If you omit a weight, it is assumed to be 1").
    """

    texts: list[str] = []
    weights: list[float] = []
    weighted = False
    for part in body.split("|"):
        weight = 1.0
        marked = WEIGHT.match(part)
        if marked is not None:
            weighted = True
            weight = min(float(marked.group(1)), MAX_WEIGHT)
            part = marked.group(2)
        if _NOTHING in part:
            # A group inside this option already drew a nothing.
            part = _settle(part)
        texts.append(part.strip())
        weights.append(weight)
    return texts, weights, weighted


def _draw(chooser: random.Random, indices: list[int], weights: list[float]) -> int:
    """One of `indices`, each in proportion to its weight. One number drawn."""

    point = chooser.random() * sum(weights[index] for index in indices)
    for index in indices:
        point -= weights[index]
        if point < 0:
            return index
    return indices[-1]


class WildcardLibrary:
    """Every wildcard under one folder, found by walking it."""

    def __init__(self, root: str | Path | None) -> None:
        self.root = Path(root) if root else None
        self._entries: dict[str, Wildcard] = {}
        self._lines: dict[str, list[str]] = {}
        self._loaded = False

    # -- discovery ---------------------------------------------------------

    def load(self, *, force: bool = False) -> "WildcardLibrary":
        """Walk the folder. Cheap enough to redo when the owner changes it."""

        if self._loaded and not force:
            return self
        self._entries = {}
        self._lines = {}
        self._loaded = True
        if self.root is None or not self.root.is_dir():
            return self

        # `resolve()` once, so every candidate is compared against the same
        # real directory rather than against the spelling the owner typed.
        try:
            real_root = self.root.resolve(strict=True)
        except OSError:
            return self

        for path in sorted(self.root.rglob(f"*{WILDCARD_SUFFIX}")):
            if len(self._entries) >= MAX_WILDCARDS:
                break
            try:
                if not path.is_file():
                    continue
                # CONTAINMENT, and the name-based check above it is not enough.
                # `_name_of` uses `relative_to(self.root)`, which is lexical:
                # a directory junction or symlink inside the root produces a
                # path that is relative to it by spelling while pointing
                # anywhere on the disk. Proven, not theorised -- a junction in
                # the wildcard folder read a file outside it and expanded the
                # contents into a prompt.
                #
                # `resolve()` follows every link in the chain, so the check
                # catches a link at any depth, not just a linked leaf.
                if not path.resolve(strict=True).is_relative_to(real_root):
                    continue
                size = path.stat().st_size
            except OSError:
                continue
            if size > MAX_FILE_BYTES:
                continue
            name = self._name_of(path)
            if name:
                self._entries[name] = Wildcard(name, path, size)
        return self

    def _name_of(self, path: Path) -> str:
        """`people/knight.txt` under the root becomes `people/knight`.

        Forward slashes on every platform, because that is what the page shows
        and what an owner types between the underscores.
        """

        try:
            relative = path.relative_to(self.root)
        except ValueError:
            return ""
        parts = [*relative.parts[:-1], relative.stem]
        return "/".join(parts).lower()

    @property
    def available(self) -> bool:
        return self.root is not None and self.root.is_dir()

    def names(self) -> list[str]:
        return sorted(self.load()._entries)

    def entries(self) -> list[dict[str, Any]]:
        return [self.load()._entries[name].to_dict() for name in self.names()]

    def __len__(self) -> int:
        return len(self.load()._entries)

    # -- content -----------------------------------------------------------

    def lines(self, name: str) -> list[str]:
        """The choosable lines of one wildcard.

        Blank lines and `#` comments are dropped: a word list an owner
        maintains by hand collects both, and neither is a thing to put in a
        prompt.
        """

        name = (name or "").strip().lower()
        self.load()
        if name in self._lines:
            return self._lines[name]
        entry = self._entries.get(name)
        if entry is None:
            return []
        try:
            raw = entry.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        found = [
            line.strip() for line in raw.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        self._lines[name] = found
        return found

    # -- expansion ---------------------------------------------------------

    def expand(self, prompt: str, seed: int | None = None) -> Expansion:
        """Replace every `__name__` with one of its lines.

        One `Random` for the whole expansion, seeded by the caller, so the same
        prompt and seed produce the same picture -- which is the entire point
        of a seed. A seed of None or -1 means "surprise me".
        """

        result = Expansion(text=prompt or "")
        if not prompt:
            return result

        # DP2. No return for want of a folder. It was written when a
        # `__name__` was all there was to expand; `{a|b}` groups came three
        # days later, below it, and read no file. With no folder every name
        # has no lines, so `_one_pass` reports it missing and leaves it as
        # typed, which is all the early return did, and the groups expand,
        # as the Extension's do with no wildcard directory.
        self.load()
        chooser = random.Random(seed if seed is not None and seed >= 0 else None)
        # DP1. The marker for a drawn "nothing" is Studio's alone.
        text = prompt.replace(_NOTHING, "")
        for depth in range(MAX_DEPTH):
            if not TOKEN.search(text) and not BRACE.search(text):
                break
            # Wildcards THEN braces, in one pass, because a line pulled out of
            # a .txt file may itself contain `{a|b}` and the next iteration has
            # to see it. Doing braces first would resolve the prompt's own
            # groups and leave the file's, which reads as intermittent.
            #
            # The loop turns on whether anything was SUBSTITUTED, not on
            # whether text remains. A missing wildcard and a declined brace
            # group are both left in the text on purpose, and testing for
            # leftovers would spin all twelve passes over them, report
            # truncation for a prompt that never recursed, and repeat every
            # warning twelve times. Comparing text before and after would not
            # do either: `__loop__` resolving to the literal `__loop__` is a
            # real substitution and must keep counting toward the bound.
            moved = self._one_pass(text, chooser, result)
            moved = self._brace_pass(moved, chooser, result)
            if not result.substituted:
                text = moved
                break
            result.substituted = False
            text = moved
        else:
            # Twelve passes and still moving: a cycle, or nesting deeper than
            # the bound. Either way it stops here and says so.
            result.truncated = True

        # DP1. Only a prompt that drew a nothing is touched.
        result.text = _settle(text) if _NOTHING in text else text
        result.missing = sorted(set(result.missing))
        # One complaint per distinct problem. A prompt with a bad group in it
        # can survive several passes, and saying the same thing four times
        # tells the owner nothing the first one did not.
        result.warnings = sorted(set(result.warnings))
        result.substituted = False
        return result

    def _one_pass(self, text: str, chooser: random.Random,
                  result: Expansion) -> str:
        def replace(match: re.Match[str]) -> str:
            name = match.group(1).lower()
            options = self.lines(name)
            if not options:
                # Left EXACTLY as written. Replacing an unknown wildcard with
                # nothing silently changes the picture; leaving the token lets
                # the owner see which one Studio could not find.
                result.missing.append(match.group(1))
                return match.group(0)
            chosen = chooser.choice(options)
            result.choices.append((name, chosen))
            result.substituted = True
            return chosen

        return TOKEN.sub(replace, text)

    def _brace_pass(self, text: str, chooser: random.Random,
                    result: Expansion) -> str:
        """One level of `{a|b|c}` and `{N$$a|b|c}`.

        The Extension's shape, read from `scripts/studio_dynamic_prompts.py`
        rather than recalled: a multi-select picks `min(N, len)` unique options
        and joins them with ", " in their ORIGINAL order, and anything that
        does not look like a choice is returned untouched.

        DP1 (owner and Moritz, 2026-11-10) goes past the Extension, which
        dropped a blank option and read `3::cat` as a word. Here `W::` is an
        option's weight and a blank option is "nothing":
        `{3::|dog}` writes nothing three times in four. A group with no weight
        and no blank draws exactly as it always has -- same call on the same
        chooser -- so an old prompt and seed still give the old words.
        Source review: `Evidence/source-review/DP1-weighted-choices.md`.

        The untouched rule is the load-bearing one. A group with no `|` is left
        exactly as written, because `{...}` also carries attention and template
        syntax, and eating those would corrupt prompts that never asked for a
        wildcard. Same for a body containing a second `$$` -- that is full
        Dynamic Prompts range/separator syntax, which this does not implement,
        and mangling it would be worse than declining it.
        """

        def replace(match: re.Match[str]) -> str:
            token, inner = match.group(0), match.group(1)

            multi = MULTI.match(inner)
            if multi is not None:
                body = multi.group(2)
                if "$$" in body:
                    return token
                try:
                    count = int(multi.group(1))
                except ValueError:  # pragma: no cover - the regex is digits
                    result.warnings.append("Invalid multi-select count")
                    return token
                texts, weights, weighted = _options(body)
                # A weight of 0 is never drawn.
                live = [index for index, weight in enumerate(weights) if weight > 0]
                if not any(texts[index] for index in live):
                    result.warnings.append("Empty multi-select")
                    return token
                if count <= 0:
                    result.warnings.append("Invalid multi-select count")
                    return token
                take = min(count, len(live))
                if weighted:
                    pool, drawn = list(live), []
                    for _ in range(take):
                        index = _draw(chooser, pool, weights)
                        pool.remove(index)
                        drawn.append(index)
                else:
                    drawn = list(live)
                    chooser.shuffle(drawn)
                    drawn = drawn[:take]
                # SORTED, so the result reads in the order the owner wrote the
                # options rather than in draw order. A drawn blank adds nothing
                # to the list.
                joined = ", ".join(texts[index] for index in sorted(drawn)
                                   if texts[index])
                result.choices.append(("choice", joined))
                result.substituted = True
                return joined or _NOTHING

            if "|" not in inner:
                return token
            texts, weights, weighted = _options(inner)
            live = [index for index, weight in enumerate(weights) if weight > 0]
            if not any(texts[index] for index in live):
                result.warnings.append("Empty inline choice")
                return token
            if weighted:
                chosen = texts[_draw(chooser, live, weights)]
            else:
                chosen = texts[live[chooser.randrange(len(live))]]
            result.choices.append(("choice", chosen))
            result.substituted = True
            return chosen or _NOTHING

        return BRACE.sub(replace, text)

    def preview(self, prompt: str, seed: int | None = None,
                samples: int = 1) -> dict[str, Any]:
        """Several expansions at once, for the page's preview panel.

        Each sample advances the sequence rather than repeating it, so an owner
        asking for five sees five different prompts -- while a fixed seed still
        gives the same five every time.
        """

        count = max(1, min(25, int(samples or 1)))
        results = []
        for index in range(count):
            step = None if seed is None or seed < 0 else seed + index
            results.append(self.expand(prompt, step))
        first = results[0]
        return {
            "prompt": first.text,
            "prompts": [item.text for item in results],
            "samples": [item.to_dict() for item in results],
            "missing": sorted({name for item in results for name in item.missing}),
            "truncated": any(item.truncated for item in results),
            "available": self.available,
            "count": len(self),
        }


def resolve_root(configured: str | Path | None,
                 fallbacks: Iterable[Path] = ()) -> tuple[Path | None, str]:
    """Which folder the wildcards come from, and how that was decided.

    Returns `(path, mode)` where mode is `custom` when the owner chose one,
    `default` when a known location was found, and `disabled` when there is
    nowhere to look. The page renders those three words differently, so
    guessing one of them would misreport the state rather than the folder.
    """

    if configured:
        path = Path(configured)
        if path.is_dir():
            return path, "custom"
        # Configured but missing: still CUSTOM. Reporting `default` would hide
        # from the owner that the folder they chose has gone.
        return path, "custom"
    for candidate in fallbacks:
        if candidate and Path(candidate).is_dir():
            return Path(candidate), "default"
    return None, "disabled"


__all__ = (
    "MAX_DEPTH",
    "MAX_FILE_BYTES",
    "MAX_WILDCARDS",
    "TOKEN",
    "WEIGHT",
    "WILDCARD_SUFFIX",
    "Expansion",
    "Wildcard",
    "WildcardLibrary",
    "resolve_root",
)
