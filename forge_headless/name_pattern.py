"""The owner's file name pattern for saved results (NM1).

Settings > Save folder > File name. Empty means Studio's own name,
`Studio-<NNNNN>-<seed>`, written by `output_layout.write_numbered` exactly as
before. A pattern uses Neo's token language (`modules/images.py`
`FilenameGenerator`, pinned `97ff3a40`) for the tokens Studio has when it
writes, plus two of Studio's own:

```text
[number]        5-digit counter, per folder (the default's NNNNN)
[mode]          create / edit / upscaled
[seed] [steps] [cfg] [shift] [sampler] [scheduler] [width] [height]
[model_name] [date] [datetime] [datetime<%Y%m%d-%H%M>] [prompt]
[prompt_spaces] [prompt_words] [prompt_hash] [prompt_hash<12>] [denoising]
```

The review, with each divergence from Neo, is
`Evidence/source-review/NM1-file-name-pattern.md`. In short: an unknown token
or a path character is refused rather than written, the pattern names a file
and never a folder, and nothing is ever overwritten.

Pure: no Forge, no Neo, no Torch.
"""

from __future__ import annotations

import datetime as _datetime
import hashlib
import re
import string
from pathlib import Path
from typing import Any, Callable, Mapping

#: Longest pattern the owner can save. Generous; the rendered stem is capped
#: separately.
MAX_PATTERN_LENGTH = 200

#: Longest rendered stem. Leaves room for a long folder under Windows' 260.
MAX_STEM_LENGTH = 150

#: Neo's per-part cap (`filenames_max_length`, default 128).
MAX_PART_LENGTH = 128

#: Neo's `directories_max_prompt_words` default.
PROMPT_WORDS = 8

#: Collision suffixes tried for a pattern without `[number]`, and counter
#: steps for one with it, before giving up.
MAX_NAME_ATTEMPTS = 10_000

#: What Studio writes when no pattern is set; shown as the field's placeholder.
DEFAULT_PATTERN = "Studio-[number]-[seed]"

# Neo's expressions (`images.py:343-345`), unchanged.
_RE_PATTERN = re.compile(r"(.*?)(?:\[([^\[\]]+)\]|$)")
_RE_PATTERN_ARG = re.compile(r"(.*)<([^>]*)>$")
_RE_NONLETTERS = re.compile(r"[\s" + re.escape(string.punctuation) + r"]+")

# Neo's Windows set (`images.py:338`). Applied on every platform: a pattern
# that works here must also work on a tester's Windows machine.
_INVALID_CHARS = '#<>:"/\\|?*\n\r\t'
# In the owner's own literal text `#` is harmless, and path characters are
# refused rather than replaced (D2).
_REFUSED_LITERAL = set('<>:"/\\|?*') | {chr(c) for c in range(32)}

_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}

_SKIP = object()  # Neo's NOTHING_AND_SKIP_PREVIOUS_TEXT


def sanitize_part(text: Any, *, replace_spaces: bool = False) -> str:
    """Neo's `sanitize_filename_part` (`images.py:350-360`)."""

    value = "" if text is None else str(text)
    if replace_spaces:
        value = value.replace(" ", "_")
    value = value.translate({ord(ch): "_" for ch in _INVALID_CHARS})
    return value.lstrip(" ")[:MAX_PART_LENGTH].rstrip(" .")


def _number(text: Any) -> str:
    """`7.0` as `7`, `5.5` as `5.5`; Neo writes the float's repr."""

    try:
        value = float(text)
    except (TypeError, ValueError):
        return ""
    return str(int(value)) if value.is_integer() else repr(value)


def _prompt_words(context: Mapping[str, Any]) -> str:
    words = [w for w in _RE_NONLETTERS.split(str(context.get("prompt") or "")) if w]
    return sanitize_part(" ".join((words or ["empty"])[:PROMPT_WORDS]))


def _datetime_token(context: Mapping[str, Any], *args: str) -> str:
    now = context.get("now") or _datetime.datetime.now()
    fmt = args[0] if args and args[0] else "%Y%m%d%H%M%S"
    try:
        text = now.strftime(fmt)
    except (ValueError, TypeError):
        text = now.strftime("%Y%m%d%H%M%S")
    return sanitize_part(text)


def _prompt_hash(context: Mapping[str, Any], *args: str) -> str:
    try:
        length = int(args[0]) if args and args[0] else 8
    except ValueError:
        length = 8
    return hashlib.sha256(str(context.get("prompt") or "").encode()).hexdigest()[:max(1, length)]


def _seed(context: Mapping[str, Any]) -> str:
    try:
        text = str(int(context.get("seed")))
    except (TypeError, ValueError):
        return "0"
    # An unresolved -1 is not a seed; the Extension writes 0 for it.
    return "0" if text.startswith("-") else text


def _denoising(context: Mapping[str, Any]) -> Any:
    value = context.get("denoising")
    return _number(value) if value else _SKIP


def _date(context: Mapping[str, Any]) -> str:
    return (context.get("now") or _datetime.datetime.now()).strftime("%Y-%m-%d")


#: token -> value. `[number]` is handled by the writer, not here.
TOKENS: dict[str, Callable[..., Any]] = {
    "seed": _seed,
    "steps": lambda c: _number(c.get("steps")),
    "cfg": lambda c: _number(c.get("cfg")),
    "shift": lambda c: _number(c.get("shift")),
    "sampler": lambda c: sanitize_part(c.get("sampler")),
    "scheduler": lambda c: sanitize_part(c.get("scheduler")),
    "width": lambda c: _number(c.get("width")),
    "height": lambda c: _number(c.get("height")),
    "model_name": lambda c: sanitize_part(c.get("model_name")),
    "mode": lambda c: sanitize_part(c.get("mode")),
    "date": _date,
    "datetime": _datetime_token,
    "prompt": lambda c: sanitize_part(c.get("prompt"), replace_spaces=True),
    "prompt_spaces": lambda c: sanitize_part(c.get("prompt")),
    "prompt_words": _prompt_words,
    "prompt_hash": _prompt_hash,
    "denoising": _denoising,
}

NUMBER_TOKEN = "number"
KNOWN_TOKENS = (NUMBER_TOKEN, *TOKENS)


def _segments(pattern: str):
    """(literal text, token name or None, args) -- Neo's `apply` loop."""

    for match in _RE_PATTERN.finditer(pattern):
        text, token = match.groups()
        if token is None:
            if text:
                yield text, None, ()
            continue
        args: list[str] = []
        while True:
            arg_match = _RE_PATTERN_ARG.match(token)
            if arg_match is None:
                break
            token, arg = arg_match.groups()
            args.insert(0, arg)
        yield text, token.lower(), tuple(args)


def pattern_problem(pattern: str) -> str:
    """Why `pattern` cannot be used, in the owner's words, or "" when it can."""

    if not isinstance(pattern, str):
        return "The file name pattern must be text."
    if len(pattern) > MAX_PATTERN_LENGTH:
        return f"The file name pattern is longer than {MAX_PATTERN_LENGTH} characters."
    unknown = []
    for text, token, _args in _segments(pattern):
        bad = sorted({ch for ch in text if ch in _REFUSED_LITERAL})
        if bad:
            shown = " ".join(repr(ch)[1:-1] if ch.isprintable() else "a control character"
                             for ch in bad)
            return f"A file name cannot contain {shown}."
        if token is not None and token not in KNOWN_TOKENS:
            unknown.append(f"[{token}]")
    if unknown:
        return f"Unknown file name token {', '.join(unknown)}."
    return ""


def validated_pattern(pattern: Any) -> str:
    """The pattern, stripped, or ValueError with `pattern_problem`'s reason."""

    text = pattern.strip() if isinstance(pattern, str) else pattern
    problem = pattern_problem(text)
    if problem:
        raise ValueError(problem)
    return text


def _finish(stem: str) -> str:
    stem = stem.strip(" ")[:MAX_STEM_LENGTH].rstrip(" .")
    if not stem:
        return "Studio"
    if stem.split(".")[0].upper() in _RESERVED:
        stem = "_" + stem
    return stem


def render(pattern: str, context: Mapping[str, Any], number: int | None = None) -> str:
    """The file stem `pattern` gives for `context` (no extension)."""

    out = ""
    for text, token, args in _segments(pattern):
        if token is None:
            out += text
            continue
        if token == NUMBER_TOKEN:
            out += text + (f"{int(number):05d}" if number is not None else "")
            continue
        function = TOKENS.get(token)
        value = function(context, *args) if function is not None else None
        if value is _SKIP:
            continue
        out += text + ("" if value is None else str(value))
    return _finish(out)


def _shape(pattern: str) -> re.Pattern:
    """Names this pattern could have produced in a folder, with `[number]`
    captured: literal text fixed, every other token wild, any extension (D4).

    Exactly five digits, as `output_layout.next_counter` reads them, so a
    number beside another numeric token (`[seed][number]`) still splits."""

    parts = []
    for text, token, _args in _segments(pattern):
        parts.append(re.escape(text))
        if token == NUMBER_TOKEN:
            parts.append(r"(\d{5})")
        elif token is not None:
            parts.append(r".*?")
    return re.compile("".join(parts) + r"(?:-\d+)?\.[^.]+", re.IGNORECASE)


def next_number(folder: Path, pattern: str) -> int:
    """The highest `[number]` among files of this pattern's shape, plus one."""

    shape = _shape(pattern)
    highest = 0
    try:
        names = [entry.name for entry in Path(folder).iterdir() if entry.is_file()]
    except OSError:
        return 1
    for name in names:
        match = shape.fullmatch(name)
        if match and match.groups():
            highest = max(highest, int(match.group(1)))
    return highest + 1


def has_number(pattern: str) -> bool:
    return any(token == NUMBER_TOKEN for _text, token, _args in _segments(pattern))


def write_patterned(folder: Path, *, pattern: str, context: Mapping[str, Any],
                    extension: str, write) -> Path:
    """Write one result under the first free name `pattern` gives.

    `write(path)` must create EXCLUSIVELY and raise `FileExistsError` when the
    name is taken, as for `output_layout.write_numbered`. With `[number]` the
    counter moves on; without it `-2`, `-3`, ... is appended (D3).
    """

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    numbered = has_number(pattern)
    start = next_number(folder, pattern) if numbered else None
    for attempt in range(MAX_NAME_ATTEMPTS):
        if numbered:
            stem = render(pattern, context, start + attempt)
        else:
            stem = render(pattern, context)
            if attempt:
                stem = f"{stem}-{attempt + 1}"
        candidate = folder / f"{stem}.{extension}"
        if candidate.exists():
            continue
        try:
            write(candidate)
        except FileExistsError:
            continue
        return candidate
    raise FileExistsError("No free result name was found in the save folder.")


__all__ = (
    "DEFAULT_PATTERN",
    "KNOWN_TOKENS",
    "MAX_PATTERN_LENGTH",
    "has_number",
    "next_number",
    "pattern_problem",
    "render",
    "sanitize_part",
    "validated_pattern",
    "write_patterned",
)
