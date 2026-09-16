"""Which routes the page calls, and what answers them. WP0.3, route half.

Regenerate:

    venv/Scripts/python.exe scripts/parity_ledger.py

Writes `docs/15_PARITY_LEDGER.md` and `docs/15_PARITY_LEDGER.json`.
`tests/studio_alpha/test_parity_ledger.py` regenerates and compares, so a new
frontend call with no disposition fails the canonical suite.

WHY THIS EXISTS, AND WHY IT IS NOT A GREP

The review bundle shipped a scan reporting 140 frontend paths, 73 exact, 18
prefix, 49 NOT FOUND. Its 49 is mostly wrong, in both directions:

  * it matched literals against two server files only, so it could not see
    `presentation.py`'s manual dispatch, the gallery prefix router, or the
    lexicon prefix -- real handlers, reported missing;
  * it was a text grep, so it silently SKIPPED `wildcard-preview.js`, which
    `file(1)` calls binary because the cache keys are NUL-separated. Two real
    call sites vanished from the count without a word.

The second is the worse failure and it dictates the design here. Every file is
read as BYTES and decoded explicitly; a file that cannot be decoded raises
rather than being passed over. `scanned_files` is part of the output so the
coverage is auditable rather than assumed.

WHAT THIS DELIBERATELY DOES NOT DO

It does not decide whether a prefix service actually answers a given path.
Gallery dispatches positionally in places (`parts[:1] == ["image"] and
len(parts) == 3`) and no literal scan can resolve that. Those are reported
`prefix-service` -- the handoff's rule is that a prefix route is never
automatically called missing. Turning one into `missing` or `working` is a
human verdict, recorded in DISPOSITIONS below with the packet that owns it.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable

APP_ROOT = Path(__file__).resolve().parents[1]
FRONTEND = APP_ROOT / "forge_studio" / "frontend"

#: Every path the page can name starts with one of these.
API_ROOTS = ("/studio/", "/api/", "/sdapi/")

#: Server files, and how each one spells its dispatch test.
SERVERS = {
    "presentation.py": (APP_ROOT / "forge_studio" / "presentation.py", "path"),
    "source_api_adapter.py": (
        APP_ROOT / "forge_studio" / "source_api_adapter.py", "route"),
    "gallery_service.py": (
        APP_ROOT / "forge_studio" / "gallery_service.py", "route"),
}

#: `gallery_service.py` matches routes with its own prefix already stripped.
GALLERY_PREFIX = "/studio/gallery"

#: Human verdicts for what the scan cannot decide. Every entry needs the packet
#: that owns it, so this table is a worklist rather than an excuse.
DISPOSITIONS: dict[str, tuple[str, str]] = {
    "/studio/checkpoints": ("missing", "WP0.3 -- back with the checkpoint catalogue or remove the entry point"),
    "/studio/checkpoint_preview": ("missing", "WP0.3 -- same decision as /studio/checkpoints"),
    "/studio/lora_metadata": ("missing", "WP4B -- LoRA catalogue"),
    "/studio/lora_preview": ("missing", "WP4B -- LoRA catalogue"),
    "/studio/open_lora_folder": ("missing", "WP4B -- native actions"),
    "/studio/civitai/fetch": ("missing", "WP11.3 -- explicit network only"),
    "/studio/civitai/private": ("missing", "WP11.3"),
    "/studio/civitai/clear_cache": ("missing", "WP11.3"),
    "/studio/develop/presets": ("missing", "WP9.1 -- Develop preset service"),
    "/studio/save_image": ("missing", "WP5.1 -- owned output pipeline replaces this"),
    "/studio/export/exr": ("missing", "WP9.3"),
    # Served since AR7.2 -- the ESRGAN half. `run_refine` and `run_ad` are
    # still refused by name, and the route still does everything it advertises,
    # but the panel's two toggles are no longer hidden: NG-4 runs the refine and
    # Auto Detail halves through the canonical generation over the upscaled
    # Canvas rather than through a second pipeline here. This route stays
    # checkpoint-free and sampling-free, which is what a 6 GB card needs.
    "/studio/upscale_and_refine": ("service-backed", "NG-4 -- ESRGAN here, refine/AD on the generation lifecycle"),
    "/studio/srgb-icc": ("missing", "WP9.1 -- colour management asset"),
    "/studio/load_vae": ("retired", "intentional -- components travel in model_selection; Generate owns readiness"),
}

#: Owner rulings on whole FEATURES, as opposed to routes or controls.
#:
#: Separate from `DISPOSITIONS` because those are keyed by path, and a feature
#: that Studio deliberately does not have has no path to key on. Without a home
#: here, "we decided not to build that" survives only in prose and gets
#: re-litigated by the next scan that notices the absence.
#:
#: SUPERSEDED IS NOT MISSING. A missing entry is work owed; a superseded one is
#: work the owner has ruled out, and reporting the two the same way is how a
#: ledger turns a decision back into a bug report.
FEATURE_DECISIONS: dict[str, tuple[str, str]] = {
    "INPAINT_SKETCH": (
        "superseded",
        "Intentionally superseded by Canvas painting and Canvas mask "
        "workflows; not a Studio 1.0 feature. Owner decision, 2026-08-19. "
        "Painting directly on the image is already a native Canvas "
        "operation, so a separate Inpaint Sketch mode would duplicate it and "
        "rebuild a Gradio-era workflow Studio replaces. The Extension's only "
        "sketch-exclusive logic is the mask dilation at "
        "`studio_generation.py:787-792` (MaxFilter, 1.5% of the short side); "
        "the rest of `_prepare_mask` is shared with ordinary inpaint and is "
        "already ported in `forge_headless/input_assets.py::decode_mask`. "
        "Residual `S.inpaintMode === \"Inpaint Sketch\"` branches remain in "
        "`canvas-core.js` (:3235, :3818) and are UNREACHABLE -- the only two "
        "assignments to that field set \"Inpaint\" -- so no owner-facing "
        "control exists and this is not a dead control."),
}


#: Whole surfaces with no standalone backend at all. Kept separate from
#: DISPOSITIONS so that "Workshop is absent" is ONE stated verdict rather than
#: twenty-five look-alike rows that could drift apart.
PREFIX_DISPOSITIONS: dict[str, tuple[str, str]] = {
    "/studio/workshop": (
        "missing",
        "WP10 -- no standalone service family; implement behind one task "
        "service rather than copying the Extension's private workers"),
}


class UnreadableSource(RuntimeError):
    """A file the ledger was asked to cover and could not decode.

    Raised rather than skipped. A ledger that quietly omits an input is the
    defect this tool exists to correct.
    """


def read_source(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as error:  # pragma: no cover - guard
        raise UnreadableSource(f"{path} is not valid UTF-8: {error}") from error


def frontend_files() -> list[Path]:
    files = sorted(FRONTEND.glob("*.js")) + sorted(FRONTEND.glob("*.html"))
    return [item for item in files if item.is_file()]


#: A quoted string or template literal that begins with an API root. The tail
#: stops at the closing quote or at the first `${`, so a built URL contributes
#: the fixed part it is built from rather than being lost.
_REFERENCE = re.compile(
    r"""["'`](?P<path>/(?:studio|api|sdapi)/[^"'`$\s]*)""")


def normalise(path: str) -> str:
    """One call site to one comparable path.

    Query strings and trailing separators are dropped: `?name=` says nothing
    about which handler answers, and `/studio/workflows/` + id is the same
    route as `/studio/workflows/`.
    """

    path = path.split("?", 1)[0].split("#", 1)[0]
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return path


#: `const API = "/studio/workshop";` -- a base every call in the file builds on.
_BASE_CONST = re.compile(
    r"""(?:const|var|let)\s+(?P<name>\w+)\s*=\s*"""
    r"""["'](?P<path>/(?:studio|api|sdapi)/[^"']*)["']""")


def _composed(text: str, name: str, base: str) -> set[str]:
    """Paths built from a base constant, which a literal scan cannot see.

    Without this, `gallery.js`'s fifty calls collapse into `/studio/gallery`
    and Workshop's whole surface becomes ONE row -- understating the largest
    missing area in the product while looking like a tidier number. The
    bundle's scan warned about exactly this and did not do it.

    Both spellings are resolved: `API + "/inspect"` and `` `${API}/inspect` ``.
    """

    joined = re.compile(
        rf"""(?:{re.escape(name)}\s*\+\s*["'](?P<a>[^"'`]*)"""
        rf"""|\$\{{{re.escape(name)}\}}(?P<b>[^"'`$\s)]*))""")
    out: set[str] = set()
    for match in joined.finditer(text):
        suffix = match.group("a") or match.group("b") or ""
        if not suffix or suffix.startswith("?"):
            out.add(normalise(base))
            continue
        if not suffix.startswith("/"):
            suffix = "/" + suffix
        out.add(normalise(base + suffix))
    return out


#: Comments, stripped before any path is attributed to a file.
#:
#: `_REFERENCE` deliberately requires the path to sit inside a quote or a
#: backtick, so that a bare word in prose cannot be mistaken for a call. That
#: is not enough here, because this codebase writes its reasoning in markdown
#: inside `//` comments, and a markdown-backticked route is byte-identical to a
#: template literal.
#:
#: Measured, not assumed: four routes were attributed to a file that never
#: calls them, three of which predate the measurement --
#:
#:     /studio/generate    <- canvas-recovery.js:75, in a comment
#:     /api/registries     <- studio-model-controls.js:717, in a comment
#:     /api/queue          <- app.js:3720, in a comment
#:
#: -- each one prose ABOUT another file's caller. A ledger that names a caller
#: which does not exist overstates coverage in the one artifact this project
#: uses to check coverage, which is the failure mode that keeps recurring here:
#: a scanner reporting a confident wrong number.
#:
#: Stripping cannot lose a real call site, because a call site inside a comment
#: is not a call site.
_LINE_COMMENT = re.compile(r"^[ \t]*//.*$", re.MULTILINE)
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)


def strip_comments(source: str) -> str:
    """Source with comments removed, for attribution scanning only.

    Line comments are matched only when the line BEGINS with `//`, so a URL
    followed by a trailing comment keeps its code intact.
    """

    source = _BLOCK_COMMENT.sub("", source)
    source = _HTML_COMMENT.sub("", source)
    return _LINE_COMMENT.sub("", source)


def frontend_references() -> tuple[dict[str, list[str]], list[str]]:
    """Every API path the page names, and every file that was read for it."""

    found: dict[str, set[str]] = {}
    scanned: list[str] = []
    for item in frontend_files():
        text = strip_comments(read_source(item))
        scanned.append(item.name)
        paths: set[str] = set()
        for match in _REFERENCE.finditer(text):
            paths.add(normalise(match.group("path")))
        for match in _BASE_CONST.finditer(text):
            paths |= _composed(text, match.group("name"),
                               match.group("path"))
        for path in paths:
            if path.startswith(API_ROOTS):
                found.setdefault(path, set()).add(item.name)
    return ({path: sorted(callers) for path, callers in sorted(found.items())},
            sorted(scanned))


def _constants(node: ast.AST) -> list[str]:
    """Every string literal directly inside one node."""

    out = []
    for item in ast.walk(node):
        if isinstance(item, ast.Constant) and isinstance(item.value, str):
            out.append(item.value)
    return out


def server_routes():
    """Exact paths and prefixes the servers claim, plus the files read.

    PARSED, not grepped. The dispatchers spell the same decision several ways --
    `path == "..."`, `parsed.path != "/studio/file"`, and `path in (...)` over a
    tuple -- and a regex per spelling is a list that silently goes out of date
    the next time someone writes a fourth one. `/studio/file` and the three
    pixel routes were reported unresolved by exactly that gap.

    Walking `ast.Compare` catches every equality and membership form at once,
    including ones not written yet.
    """

    exact: set[str] = set()
    prefixes: set[str] = set()
    exact_owner: dict[str, str] = {}
    prefix_owner: dict[str, str] = {}
    scanned: list[str] = []
    for name, (path, _variable) in SERVERS.items():
        text = read_source(path)
        scanned.append(name)
        base = GALLERY_PREFIX if name == "gallery_service.py" else ""

        def claim(value: str) -> str | None:
            if not value.startswith("/"):
                return None
            # Gallery matches with its own prefix already stripped.
            if base and not value.startswith(API_ROOTS):
                value = base + value
            return normalise(value) if value.startswith(API_ROOTS) else None

        routing = delegating_prefixes(text)
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare) and all(
                    isinstance(op, (ast.Eq, ast.NotEq, ast.In))
                    for op in node.ops):
                for value in _constants(node):
                    claimed = claim(value)
                    if claimed:
                        exact.add(claimed)
                        exact_owner.setdefault(claimed, name)
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "startswith"
                    and node.args):
                for value in _constants(node.args[0]):
                    claimed = claim(value)
                    if not claimed:
                        continue
                    # A branch that forwards to another dispatcher is
                    # ROUTING, not answering, and owns nothing. See
                    # `delegating_prefixes` for the two wrong answers this
                    # replaced.
                    if claimed in routing:
                        continue
                    prefixes.add(claimed)
                    prefix_owner.setdefault(claimed, name)
        # A prefix router claims its own base even when the base never appears
        # in a comparison, which is how `/studio/gallery` itself was reported
        # missing by the bundle's scan.
        if base:
            prefixes.add(base)
            prefix_owner.setdefault(base, name)
    # Literal route tables that are not `==` comparisons.
    prefixes.add("/studio/static")
    prefix_owner.setdefault("/studio/static", "presentation.py")
    return exact, prefixes, sorted(scanned), exact_owner, prefix_owner


#: Literal spellings of "this service is not here". There is no shared helper,
#: constant or decorator marking them -- three bespoke idioms grew separately,
#: all rendered at HTTP 200 because `presentation.py` wraps every adapter return
#: in HTTPStatus.OK. Matching text is unlovely and it is what the source gives.
UNAVAILABLE_MARKERS = ("unavailable", "not available", "available", "no ")

#: Filled by `build()`; module level so `classify` stays a pure lookup.
HANDLER_KINDS: dict = {}
SHADOWED: dict = {}

#: A branch mentioning one of these hands the request to another dispatcher.
DELEGATES_TO = ("_source_adapter", "_gallery", "_adapter")


def delegating_prefixes(text: str) -> set[str]:
    """Prefixes whose branch ROUTES the request rather than answering it.

    `presentation.py` tests `path.startswith("/studio/")` and
    `path.startswith("/sdapi/v1/")` and then calls
    `self._source_adapter.get(...)`. Those branches own nothing; the adapter
    does. Counting them as owners produced two separate wrong answers:

      * `/studio/` resolved EVERY route in the product, giving a ledger with
        nothing missing -- flattering and false;
      * `/sdapi/v1` made `source_api_adapter.py`'s own `/sdapi/v1/options`
        branch look shadowed by the very file that forwards to it.

    The first was patched by requiring a prefix to have a path segment of its
    own, which is a heuristic that happens to exclude `/studio/` and happens
    not to exclude `/sdapi/v1`. Delegation is the real property, so it is what
    is measured now.
    """

    found: set[str] = set()
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.If):
            continue
        prefixes: list[str] = []
        for sub in ast.walk(node.test):
            if (isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "startswith" and sub.args):
                prefixes.extend(_constants(sub.args[0]))
        if not prefixes:
            continue
        names = {item.attr for item in ast.walk(node)
                 if isinstance(item, ast.Attribute)}
        if names.intersection(DELEGATES_TO):
            found.update(normalise(value) for value in prefixes
                         if value.startswith("/"))
    return found


def _branch_paths(test: ast.AST) -> list[str]:
    """The route literals an `if` tests for, in any of the spellings used."""

    found = []
    for node in ast.walk(test):
        if isinstance(node, ast.Compare) and all(
                isinstance(op, (ast.Eq, ast.In)) for op in node.ops):
            for value in _constants(node):
                if value.startswith(API_ROOTS):
                    found.append(normalise(value))
    return found


def _body_kind(body: list[ast.stmt]) -> tuple[str, str]:
    """What a handler branch DOES, as far as a parse can tell.

    Three outcomes, and the third one is the honest one:

    `service-backed`  the body calls a collaborator. NOT a claim that the
                      feature works -- only that this branch hands off to
                      something rather than answering from a literal.
    `capability-gated` the body answers from a literal that says the service is
                      not there. These are the convincing dead controls: real
                      route, real 200, empty payload.
    `scan-uncertain`   a parse cannot settle it. WP0.3 names this status for
                      exactly that, and using it is better than guessing.

    The uncertain case is not hypothetical. `/studio/task_id` returns
    `{"task_id": self.active_job_id}` -- a property READ, no call -- so a
    "contains no call" rule would file a working route as a stub.
    """

    calls, attributes, literals = [], [], []
    for statement in body:
        for node in ast.walk(statement):
            if isinstance(node, ast.Call):
                target = node.func
                if isinstance(target, ast.Attribute):
                    calls.append(target.attr)
            elif isinstance(node, ast.Attribute):
                attributes.append(node.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                literals.append(node.value.lower())

    if calls:
        return "service-backed", f"delegates to {sorted(set(calls))[0]}()"
    text = " ".join(literals)
    if any(marker in text for marker in UNAVAILABLE_MARKERS):
        return ("capability-gated",
                "answers from a literal that reports the service unavailable")
    # ORDER MATTERS, and getting it wrong reintroduces the exact trap this
    # function documents. A state READ must be checked BEFORE the
    # literal-payload rule: `return {"task_id": self.active_job_id}` is a dict,
    # so a payload-first rule files a working route as a hardcoded stub. The
    # first draft did precisely that and the test below caught it.
    if attributes:
        return ("scan-uncertain",
                "reads state without calling anything; a parse cannot tell a "
                "working property read from a stub")
    if any(isinstance(node, (ast.List, ast.Dict))
           for statement in body for node in ast.walk(statement)):
        return ("capability-gated",
                "answers with a hardcoded literal payload, calling nothing")
    return "scan-uncertain", "no decidable body pattern"


def handler_kinds() -> dict[str, tuple[str, str]]:
    """Every adapter route path, mapped to what its branch body does."""

    tree = ast.parse(read_source(SERVERS["source_api_adapter.py"][0]))
    kinds: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        paths = _branch_paths(node.test)
        if not paths:
            continue
        kind = _body_kind(node.body)
        for path in paths:
            # FIRST branch wins, which is how the dispatcher itself behaves.
            kinds.setdefault(path, kind)
    return kinds


def shadowed_paths(exact_owner: dict[str, str],
                   prefix_owner: dict[str, str]) -> dict[str, str]:
    """Exact branches a DIFFERENT file's prefix router answers first.

    This is the WP0.4 defect generalised. There, two `if` branches claimed
    `/studio/dynamic_prompts/config` and the second -- the only one that stored
    anything -- was unreachable, so the toggle could not be turned off and
    nothing failed.

    THE OWNING FILE IS THE WHOLE TEST, and the first version of this function
    omitted it. Flagging any exact path that falls under any prefix reported
    nine shadowed routes, and all nine were wrong: `/studio/lexicon/file/save`
    and friends are matched INSIDE `source_api_adapter.py`'s own
    `route.startswith("/studio/lexicon/")` block, and the `/studio/gallery/...`
    exacts come from `gallery_service.py`, which is also the file that owns
    that prefix. A branch nested inside its own router is ordinary dispatch.

    Shadowing requires two different files: one claiming a prefix, another
    claiming an exact path beneath it. Same file, no finding.
    """

    shadowed = {}
    for path, owner in sorted(exact_owner.items()):
        for prefix, prefix_file in sorted(prefix_owner.items(),
                                          key=lambda item: -len(item[0])):
            if prefix == "/studio/static" or path == prefix:
                continue
            if not path.startswith(prefix + "/"):
                continue
            if prefix_file == owner:
                # Its own router. Normal nesting.
                break
            shadowed[path] = (
                f"{owner} has a branch for this path, but {prefix_file} owns "
                f"{prefix} and answers first -- the branch may be UNREACHABLE "
                f"(same shape as the WP0.4 duplicate handler); confirm the "
                f"dispatch order before acting")
            break
    return shadowed


def classify(path: str, exact: set[str], prefixes: set[str]) -> tuple[str, str]:
    if path in DISPOSITIONS:
        status, note = DISPOSITIONS[path]
        return status, note
    for prefix, (status, note) in PREFIX_DISPOSITIONS.items():
        if path == prefix or path.startswith(prefix + "/"):
            return status, note
    if path in SHADOWED:
        return "shadowed", SHADOWED[path]
    if path in exact:
        # "A handler answers" was the whole answer in the first increment, and
        # it flattered the product: a route that resolves to a hardcoded empty
        # list is how this interface comes to look more finished than it is.
        # The body now decides, and `scan-uncertain` is a real answer rather
        # than a guess dressed as one.
        return HANDLER_KINDS.get(
            path, ("scan-uncertain", "a handler answers, body not classified"))
    for prefix in sorted(prefixes, key=len, reverse=True):
        if path == prefix or path.startswith(prefix + "/"):
            return "prefix-service", f"resolved to the service behind {prefix}"
    return "unresolved", "NO DISPOSITION -- add one to DISPOSITIONS or wire it"


# ---------------------------------------------------------------------------
# The CONTROL half. Which visible controls exist, and whether anything reads
# them.
#
# A route can resolve while the control that calls it reaches nothing, and the
# reverse: a control can exist with no collector reading it at all. The route
# table cannot see either case, and "mature frontend mistaken for backend
# parity" is the program's named top risk -- so the controls get their own pass.
# ---------------------------------------------------------------------------

#: Tags that are controls. `role="button"` is included because the Canvas rail
#: uses divs for tools.
CONTROL_TAGS = ("button", "input", "select", "textarea")

#: The Generate tab, which is one contiguous region of index.html.
GENERATE_REGION_ID = "page-generate"

#: Visibility and enablement, expressed five statically discoverable ways.
#: Recorded per control so a disposition can say WHY something is not reachable.
GATE_ATTRIBUTES = ("data-gate", "hidden", "disabled")
GATE_CLASSES = ("arch-hidden", "param-advanced")

#: Controls with no collector, ruled on by hand. The gate below fails on any
#: visible control that is neither collected nor listed here, which is WP0.3's
#: "fail CI when a new visible control has no ledger disposition".
CONTROL_DISPOSITIONS: dict[str, str] = {
    "adLoraAdd1": "WP3/WP4B -- per-slot Auto Detail LoRA stacks are not implemented",
    "adLoraAdd2": "WP3/WP4B -- as adLoraAdd1",
    "adLoraAdd3": "WP3/WP4B -- as adLoraAdd1",
    "cnUploadBtn1": "WP6A -- ControlNet has no standalone backend at all",
    "cnUploadBtn2": "WP6A -- as cnUploadBtn1",
    "layerAddBrightness": "WP9.1 -- Develop adjustment layers",
    "layerAddHue": "WP9.1 -- Develop adjustment layers",
    "layerAddLevels": "WP9.1 -- Develop adjustment layers",
}


class _Controls(HTMLParser):
    """Every control tag, and whether it sits inside the Generate region.

    A real parser rather than a line regex: four control tags in index.html
    span multiple lines, and a regex over lines silently drops them -- the same
    class of quiet omission the route half was built to stop.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.controls: list[dict] = []
        self._depth = 0
        self._generate_depth: int | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = {name: (value or "") for name, value in attrs}
        if tag == "div":
            self._depth += 1
            if attributes.get("id") == GENERATE_REGION_ID:
                self._generate_depth = self._depth
            return
        if tag not in CONTROL_TAGS and attributes.get("role") != "button":
            return
        key, keyed_by = attributes.get("id", ""), "id"
        for attribute in ("data-ar", "data-base"):
            if not key and attributes.get(attribute):
                key, keyed_by = attributes[attribute], attribute
        classes = attributes.get("class", "").split()
        gates = [name for name in GATE_ATTRIBUTES if name in attributes]
        gates += [name for name in GATE_CLASSES if name in classes]
        if "display:none" in attributes.get("style", "").replace(" ", ""):
            gates.append("style=display:none")
        self.controls.append({
            "key": key,
            "keyed_by": keyed_by,
            "tag": tag,
            "in_generate": self._generate_depth is not None,
            "gates": sorted(set(gates)),
        })

    def handle_endtag(self, tag: str) -> None:
        if tag != "div":
            return
        if self._generate_depth == self._depth:
            self._generate_depth = None
        self._depth -= 1


def submitted_ids() -> set[str]:
    """Element ids the Generate request collector actually reads.

    `doGenerate()` builds one object literal at `const params = {` and reads its
    inputs through `getElementById("...")` and the `_num("...", d)` helper. This
    is scoped to THAT literal, not to the whole file: an id read somewhere else
    in app.js is scripted, but it is not part of the generation request, and
    conflating the two is how "the control exists" becomes "the control works".
    """

    text = read_source(FRONTEND / "app.js")
    ids: set[str] = set()
    for anchor in _SUBMISSION_ANCHORS:
        start = text.find(anchor)
        while start >= 0:
            block = _balanced_block(text, start)
            # EVERY quoted identifier in the block, not just the ones passed
            # straight to `getElementById`. `_aspectGroup` reads its controls
            # through local helpers -- `on("arRandBase")`, `pool("arBasePoolData")`
            # -- so a getElementById-only rule reported five freshly wired
            # controls as unsubmitted. The caller intersects this with the real
            # control ids, so a stray string cannot invent one.
            ids |= set(re.findall(r'["\']([A-Za-z][\w-]{2,})["\']', block))
            start = text.find(anchor, start + len(anchor))
    return ids


#: Every construct that feeds the generation request.
#:
#: `const params` alone was the first version, and it reads only the LEGACY
#: collector -- so every control submitted through the lifecycle body was
#: reported "scripted, but not part of the generation request", which became
#: actively false once WP1.5 wired the aspect randomizer. A ledger that
#: measures one of two collectors describes a product that has one.
#:
#: The `_*Group()` helpers are listed because the lifecycle body SPREADS them
#: (`...(_aspectGroup() ? {aspect: ...} : {})`), so the ids they read are
#: submitted without appearing in the literal itself.
_SUBMISSION_ANCHORS = (
    # `const params = {` USED TO BE HERE, and it is the legacy collector that
    # sits below the lifecycle `return` -- a route no install takes. Counting
    # it as a submission route is how this ledger reported both Batch controls
    # as healthy right up until they were deleted, and how it covered for the
    # three group anchors below that used to match nothing.
    #
    # Removing it surfaced exactly two controls, one real and one not:
    #   paramHrCheckpoint  genuinely unsubmitted -- now hidden, see index.html
    #   paramSeed          a false positive: it IS sent, through
    #                      `_resolveSubmittedSeed`, which is a named function
    #                      outside every anchor block. Hence the entry below.
    "const jobParams = {",
    "function _resolveSubmittedSeed",
    # THESE WERE WRONG, and silently. They read `function _hiresGroup` and so
    # on, while the source declares arrow consts -- so three of the four
    # matched NOTHING and the ids those groups read were credited to the
    # legacy `const params` collector instead. The ledger reported them
    # correctly by accident, through a route that does not run.
    "const _hiresGroup",
    "const _autoDetailGroup",
    "const _variationGroup",
    "function _aspectGroup",
    # Added because the live branch spreads them too, and a group missing from
    # this list makes its controls look unbound.
    "const _outputGroup",
    "const _softInpaintGroup",
    # The prompt is read through a helper rather than inline, so without this
    # the ledger called `paramPrompt` "not part of the generation request".
    "const _preparedPrompt",
    # Clip Skip, for the same reason as `_resolveSubmittedSeed`: the live body
    # spreads the field conditionally and the element is read inside a named
    # function outside every other anchor. Without this the ledger reported a
    # freshly wired control as `scripted -- not part of the generation
    # request`, which is the precise claim this table exists to get right.
    "function _clipSkipValue",
)


def _balanced_block(text: str, start: int) -> str:
    """From the first `{` at or after `start` to its matching close."""

    opening = text.find("{", start)
    if opening < 0:
        return ""
    depth = 0
    for position in range(opening, len(text)):
        if text[position] == "{":
            depth += 1
        elif text[position] == "}":
            depth -= 1
            if depth == 0:
                return text[opening:position + 1]
    return text[opening:]


def scripted_ids() -> set[str]:
    """Every id named anywhere in the frontend scripts.

    The first draft of this read app.js ALONE and reported 73 uncollected
    controls out of 146 -- a number that looked like a devastating finding and
    was mostly my own blind spot, since `canvas-ui.js`, `settings-page.js`,
    `studio-model-controls.js` and a dozen more bind their own ids. The same
    single-file mistake as the scan this ledger replaced.

    Any quoted occurrence counts. That is deliberately generous: the useful
    finding is a control NOTHING names, and a broad rule makes that claim safe
    to trust when it fires.
    """

    found: set[str] = set()
    for item in frontend_files():
        if item.suffix != ".js":
            continue
        found |= set(re.findall(r'["\'`]([A-Za-z][\w-]{2,})["\'`]',
                                read_source(item)))
    return found


def controls() -> list[dict]:
    """Visible Generate-tab controls, with a status for each."""

    parser = _Controls()
    parser.feed(read_source(FRONTEND / "index.html"))
    submits, scripted = submitted_ids(), scripted_ids()
    scripted_text = "".join(
        read_source(item) for item in frontend_files()
        if item.suffix == ".js")
    rows = []
    for control in parser.controls:
        if not control["in_generate"]:
            continue
        key = control["key"]
        if not key:
            status, note = "unkeyed", "no id or data attribute to key on"
        elif control["keyed_by"] != "id":
            # `1:1`, `16:9`, `768` are VALUES. The handler binds
            # `querySelectorAll("[data-ar]")`, so asking whether "16:9" appears
            # in the scripts is the wrong question -- it produced twelve
            # confident false findings before this branch existed.
            attribute = control["keyed_by"]
            bound = attribute in scripted_text
            status = "delegated" if bound else "unbound"
            note = (f"selected collectively by [{attribute}]"
                    if bound else
                    f"[{attribute}] is never selected in any script")
        elif key in submits:
            status = "submitted"
            note = "read by the Generate request collector"
        elif key in scripted:
            # NOT a claim that it works -- only that something binds it. Whether
            # that handler reaches a service is the route half's question.
            status = "scripted"
            note = "bound by a frontend script, but not part of the "\
                   "generation request"
        elif control["gates"]:
            status = "gated"
            note = f"nothing binds it; gated by {', '.join(control['gates'])}"
        elif key in CONTROL_DISPOSITIONS:
            status, note = "dispositioned", CONTROL_DISPOSITIONS[key]
        else:
            status = "unbound"
            note = ("VISIBLE and NOTHING in the frontend names this id -- "
                    "wire it, hide it, or give it a CONTROL_DISPOSITIONS entry")
        rows.append({"key": key, "tag": control["tag"], "status": status,
                     "note": note, "gates": control["gates"]})
    return sorted(rows, key=lambda row: (row["status"], row["key"]))


def build() -> dict:
    global HANDLER_KINDS, SHADOWED
    references, frontend_scanned = frontend_references()
    exact, prefixes, server_scanned, exact_owner, prefix_owner = server_routes()
    HANDLER_KINDS = handler_kinds()
    SHADOWED = shadowed_paths(exact_owner, prefix_owner)
    rows = []
    for path, callers in references.items():
        status, note = classify(path, exact, prefixes)
        rows.append({"path": path, "status": status, "note": note,
                     "callers": callers})
    totals: dict[str, int] = {}
    for row in rows:
        totals[row["status"]] = totals.get(row["status"], 0) + 1
    control_rows = controls()
    control_totals: dict[str, int] = {}
    for row in control_rows:
        control_totals[row["status"]] = control_totals.get(row["status"], 0) + 1
    return {
        "schema_version": 2,
        "scanned_files": {"frontend": frontend_scanned,
                          "servers": server_scanned},
        "totals": {"referenced": len(rows), **dict(sorted(totals.items()))},
        "routes": rows,
        "control_totals": {"generate_tab": len(control_rows),
                           **dict(sorted(control_totals.items()))},
        "controls": control_rows,
        "feature_decisions": [
            {"feature": name, "status": status, "note": note}
            for name, (status, note) in sorted(FEATURE_DECISIONS.items())
        ],
    }


def render(ledger: dict) -> str:
    totals = ledger["totals"]
    lines = [
        "# Parity ledger — routes",
        "",
        "GENERATED by `scripts/parity_ledger.py`. Do not edit by hand;",
        "`tests/studio_alpha/test_parity_ledger.py` regenerates and compares.",
        "",
        "READ THE STATUSES NARROWLY. None of them says a feature works —",
        "only a browser journey says that.",
        "",
        "| status | means |",
        "|---|---|",
        "| `service-backed` | the handler delegates to a collaborator. The "
        "strongest thing a parse can say, and still not proof it works. |",
        "| `capability-gated` | the handler answers from a literal that "
        "reports the service unavailable, or a hardcoded empty payload. These "
        "are the convincing dead controls: real route, real HTTP 200, nothing "
        "behind it. |",
        "| `prefix-service` | a prefix router owns the path. Gallery dispatches "
        "positionally in places, so this is resolution of the OWNER, not of "
        "the route. |",
        "| `scan-uncertain` | a parse cannot settle it — a property read looks "
        "like a stub, and guessing either way would be worse than saying so. |",
        "| `shadowed` | another file's prefix router answers first, so the "
        "branch may be unreachable. The WP0.4 defect class. |",
        "| `missing` / `retired` | human verdicts from the tables in the "
        "script, each carrying the packet that owns it. |",
        "",
        "## Totals",
        "",
        "```text",
    ]
    for key, value in totals.items():
        lines.append(f"{key:<16} {value}")
    lines += ["```", "",
              f"Frontend files read: {len(ledger['scanned_files']['frontend'])}",
              "(read as bytes and decoded explicitly — a file that cannot be",
              "decoded fails the run rather than being skipped)", "",
              "## Routes", "",
              "| Path | Status | Owner / note | Called from |",
              "|---|---|---|---|"]
    for row in ledger["routes"]:
        callers = ", ".join(row["callers"])
        lines.append(
            f"| `{row['path']}` | {row['status']} | {row['note']} | {callers} |")

    lines += [
        "", "## Generate-tab controls", "",
        "Extracted with a real HTML parser: four control tags in `index.html`",
        "span lines, and a line regex drops them without saying so.",
        "",
        "| status | means |",
        "|---|---|",
        "| `submitted` | read by the Generate request collector — the only "
        "status that touches generation |",
        "| `scripted` | a frontend script binds the id. NOT a claim that it "
        "reaches a service; that is the route table's question |",
        "| `delegated` | selected collectively by an attribute such as "
        "`[data-ar]`, never by id |",
        "| `gated` | nothing binds it, but it is hidden or disabled, so it is "
        "not an owner-visible lie |",
        "| `dispositioned` | unbound, and ruled on by hand with the packet "
        "that owns it |",
        "| `unbound` | VISIBLE and nothing in the frontend names it. The "
        "dead-control class this program exists to eliminate; the build fails "
        "on one |",
        "", "```text",
    ]
    for key, value in ledger["control_totals"].items():
        lines.append(f"{key:<16} {value}")
    lines += ["```", "", "| Control | Tag | Status | Note |", "|---|---|---|---|"]
    for row in ledger["controls"]:
        lines.append(f"| `{row['key'] or '(unkeyed)'}` | {row['tag']} | "
                     f"{row['status']} | {row['note']} |")

    lines += [
        "", "## Feature decisions", "",
        "Rulings on whole features, which have no route or control to key on.",
        "",
        "| status | means |",
        "|---|---|",
        "| `superseded` | the owner has ruled the feature out because Studio "
        "solves it another way. NOT a gap, and not work owed. |",
        "",
        "| Feature | Status | Decision |",
        "|---|---|---|",
    ]
    for row in ledger["feature_decisions"]:
        lines.append(f"| `{row['feature']}` | {row['status']} | {row['note']} |")
    return "\n".join(lines) + "\n"


def main(argv: Iterable[str] = ()) -> int:
    ledger = build()
    (APP_ROOT / "docs" / "15_PARITY_LEDGER.json").write_text(
        json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    (APP_ROOT / "docs" / "15_PARITY_LEDGER.md").write_text(
        render(ledger), encoding="utf-8")
    totals = ledger["totals"]
    print(" ".join(f"{key}={value}" for key, value in totals.items()))
    controls_line = ledger["control_totals"]
    print(" ".join(f"control_{key}={value}"
                   for key, value in controls_line.items()))
    # An undisposed route OR an undisposed visible control fails the run. WP0.3
    # asks for exactly this, and a ledger that only reports is a ledger that
    # gets ignored -- the bundle's duplicate count proved that.
    return 1 if (totals.get("unresolved") or controls_line.get("unbound")) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
