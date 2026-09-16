"""Read JavaScript source as CODE, with comments removed.

WHY THIS IS A SHARED MODULE AND NOT A COPY IN EACH SUITE. Word-ban guards over
JS have now failed on their own documentation seven times in this repository:
a test asserting "this module never touches a canvas" matches the comment
explaining that it never touches a canvas. The Python side solves it with `ast`;
JS has no stdlib parser, so this scanner is the equivalent -- and a second copy
of it is the same mistake as a second content-hash function, which AR5.4 spent a
package proving.

Underscore-prefixed so `discover(pattern="test_*.py")` does not collect it, the
same convention `_live_path_probe.py` and `_internal_alpha_probe.py` use.

Its own correctness is asserted by `TheCommentStripperWorksTests` in
`test_v2_brush_contracts.py`, because a stripper that returned "" would make
every guard it serves pass.
"""

from __future__ import annotations

from pathlib import Path


def code_only(source: str) -> str:
    """`source` with `//` and `/* */` comments removed, strings preserved.

    A two-state scanner rather than a regex: it steps over string literals and
    their escapes, so a `//` inside a URL is not mistaken for a comment. It does
    not reformat -- whitespace left by a removed trailing comment stays, because
    removing comments and tidying code are two jobs and only one is asserted.

    KNOWN LIMIT, stated rather than discovered later: a regex literal whose body
    contains `//` or `/*` would be misread as a comment. No module this guards
    contains one, and the calibration test asserts the real files' exported
    surfaces survive, which is what would break if that ever changed.
    """

    out: list[str] = []
    i, n, state = 0, len(source), None
    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if state is None:
            if ch == "/" and nxt == "/":
                state, i = "line", i + 2
            elif ch == "/" and nxt == "*":
                state, i = "block", i + 2
            elif ch in "'\"`":
                state, i = ch, i + 1
                out.append(ch)
            else:
                out.append(ch)
                i += 1
        elif state == "line":
            if ch == "\n":
                state = None
                out.append(ch)
            i += 1
        elif state == "block":
            if ch == "*" and nxt == "/":
                state, i = None, i + 2
            else:
                i += 1
        else:                                   # inside a string literal
            if ch == "\\":
                out.append(ch)
                if i + 1 < n:
                    out.append(source[i + 1])
                i += 2
                continue
            out.append(ch)
            if ch == state:
                state = None
            i += 1
    return "".join(out)


def code_of(path: Path) -> str:
    """One file's code, comments removed."""

    return code_only(Path(path).read_text(encoding="utf-8"))
