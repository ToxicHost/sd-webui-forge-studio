"""Check for Updates and Update Now, for a Git checkout. U1.

The page (`app.js` `UpdateBanner`, the Settings "Check for Updates" button)
calls `/studio/api/check-update`, `/studio/api/update` and
`/studio/api/update-status`; Standalone answered all three with mock text.

The Extension's updater (`scripts/studio_api.py:6717-6864`) asks the GitHub API
for the branch head and overlays a downloaded zip on its folder. Standalone is
installed differently: testers `git clone` the release branch and update with
`git pull --ff-only` (`docs/studio/GIT_INSTALL.md`). A zip overlay would leave
every changed file "modified" against the old commit, and the documented
`git pull --ff-only` would then refuse. So this updater is that documented
command, run for the owner:

  * check: fetch the branch this checkout tracks, then count what it is behind;
  * update: fast-forward only -- never a merge, reset or clean -- then ask for a
    restart, because the running server is still the old code.

It refuses, with a reason the page shows, when the copy is not a Git checkout,
tracks no remote branch, or has its own commits (diverged). A developer tree is
one of those, so it is never touched.

U4. Edited files no longer refuse an update. One the update does not change
is kept, as Git's fast-forward keeps it. One the update DOES change is named
to the page, which asks the owner: replace it (their copy is backed up in
`update-backups/` first) or keep theirs (the update then can't promise the two
work together). Owner's decision, 2026-11-13; the Extension overwrites without
asking. Every check and update is logged. Review:
Evidence/source-review/U4-edited-files-ask.md

U3. Releases moved from `release/tester-alpha-prep` to `main`. Installed copies
fetch their branch by name, so a copy that follows the old one is pointed at
`main` the next time it checks or updates. Nothing else moves it: Studio goes
online only when the owner presses, so a copy updated by hand with `git pull`
gets a console line at startup instead (`follows_retired_branch`).

Source review: Evidence/source-review/U1-check-for-updates.md,
Evidence/source-review/U3-move-to-main.md
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("studio.update")

#: U4. Where a replaced file's edited copy goes, inside the Studio folder.
BACKUP_FOLDER = "update-backups"
#: The page's answers when the update changes a file the owner edited.
REPLACE, KEEP = "replace", "keep"

_FETCH_TIMEOUT = 120
_LOCAL_TIMEOUT = 30
#: U3. The old release branch stays on GitHub, frozen on the last release that
#: can move a copy: anyone further behind updates to it, then moves.
_RETIRED_BRANCH = "release/tester-alpha-prep"
_RELEASE_BRANCH = "main"


class UpdateService:
    def __init__(self, checkout: Any, *, run: Callable[..., Any] = subprocess.run) -> None:
        self._root = Path(checkout)
        self._run = run
        self._lock = threading.Lock()
        self._progress = {"phase": "idle", "pct": 0, "message": ""}

    # -- git --------------------------------------------------------------------

    def _git(self, *args: str, timeout: int = _LOCAL_TIMEOUT, raw: bool = False) -> tuple[int, str, str]:
        """`raw` keeps stdout exactly as Git wrote it: a porcelain line starts
        with a meaningful space (" M path"), which stripping would eat."""

        git = shutil.which("git")
        if git is None:
            raise _Refusal("Git isn't installed, so Studio can't update itself. "
                           "Install Git for Windows, or download the new version by hand.")
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "LC_ALL": "C"}
        # U2. Git refuses a repository whose folder another Windows account
        # owns ("dubious ownership") -- the usual state of a clone on a second
        # drive or one made from an elevated prompt -- and every command,
        # `rev-parse` included, fails. Studio then said the copy "wasn't
        # installed with Git" to a tester who had just cloned it. Trusting the
        # folder for THESE commands only is safe: `-C` pins them to Studio's
        # own install, whose code is already running, and nothing is written
        # to the owner's Git configuration. `-c` is protected configuration,
        # which `safe.directory` honours.
        trust = ["-c", f"safe.directory={self._root.resolve().as_posix()}"]
        try:
            done = self._run([git, *trust, "-C", str(self._root), *args], capture_output=True,
                             text=True, timeout=timeout, env=env, check=False)
        except subprocess.TimeoutExpired:
            raise _Refusal("Git took too long to answer.", offline=args[:1] == ("fetch",)) from None
        except OSError as error:
            raise _Refusal(f"Git could not be run: {error.strerror or error}") from None
        out = done.stdout or ""
        return done.returncode, (out if raw else out.strip()), (done.stderr or "").strip()

    def _tracked(self) -> tuple[str, str, str]:
        """`(remote, branch, upstream)` for the checked-out branch, or a refusal."""

        code, inside, err = self._git("rev-parse", "--is-inside-work-tree")
        if code != 0 or inside != "true":
            # U2. A `.git` that Git cannot read is not "not installed with
            # Git"; say what Git said instead.
            if (self._root / ".git").exists():
                raise _Refusal("Git couldn't read this copy of Studio, so it can't update "
                               "itself" + (f": {err.splitlines()[0][:200]}" if err else "."),
                               detail=err)
            raise _Refusal("This copy of Studio wasn't installed with Git, so it can't "
                           "update itself. Download the new version from GitHub instead.")
        code, upstream, _ = self._git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        if code != 0 or "/" not in upstream:
            raise _Refusal("This copy doesn't follow a release branch, so it can't update "
                           "itself.")
        code, remote, _ = self._git("config", "--get", f"branch.{self._branch()}.remote")
        remote = remote if code == 0 and remote else upstream.split("/", 1)[0]
        branch = upstream[len(remote) + 1:] if upstream.startswith(remote + "/") else upstream.split("/", 1)[1]
        return remote, branch, upstream

    def _follow_release_branch(self, remote: str, branch: str, upstream: str) -> tuple[str, str, str]:
        """U3. Point a copy on the retired branch at `main`, if that loses nothing.

        Returns what the copy now follows. Files and HEAD are not touched, and
        every way this can fail leaves the copy on the old branch to try again
        at the next press. Only a copy with no commits of its own, whose HEAD is
        already part of `main`, moves; anything else (a fork whose `main` is
        unrelated, say) carries on as it was.
        """

        stay = remote, branch, upstream
        if branch != _RETIRED_BRANCH or self._status(upstream)["ahead"]:
            return stay
        tracking = f"refs/remotes/{remote}/{_RELEASE_BRANCH}"
        # A named refspec: a `--branch` clone's own refspec covers only the old
        # branch, so a plain `fetch origin main` would update no tracking ref.
        code, _, _ = self._git("fetch", "--quiet", remote, f"+refs/heads/{_RELEASE_BRANCH}:{tracking}",
                               timeout=_FETCH_TIMEOUT)
        if code != 0 or self._git("merge-base", "--is-ancestor", "HEAD", tracking)[0] != 0:
            return stay
        _, refspecs, _ = self._git("config", "--get-all", f"remote.{remote}.fetch")
        single_branch = "refs/heads/*" not in refspecs
        if single_branch and tracking not in refspecs:
            if self._git("remote", "set-branches", "--add", remote, _RELEASE_BRANCH)[0] != 0:
                return stay
        # The move itself: one config write.
        if self._git("branch", f"--set-upstream-to={remote}/{_RELEASE_BRANCH}", self._branch())[0] != 0:
            return stay
        if single_branch:
            # So a later `git pull` no longer depends on the old branch. Failing
            # here costs nothing: the copy has already moved.
            self._git("remote", "set-branches", remote, _RELEASE_BRANCH)
        return remote, _RELEASE_BRANCH, f"{remote}/{_RELEASE_BRANCH}"

    def follows_retired_branch(self) -> bool:
        """U3. Local only, for the startup line: this asks Git, not GitHub."""

        try:
            return self._tracked()[1] == _RETIRED_BRANCH
        except _Refusal:
            return False

    def _branch(self) -> str:
        code, branch, _ = self._git("rev-parse", "--abbrev-ref", "HEAD")
        return branch if code == 0 else "HEAD"

    def _fetch(self, remote: str, branch: str) -> None:
        code, _, err = self._git("fetch", "--quiet", remote, branch, timeout=_FETCH_TIMEOUT)
        if code == 0:
            return
        # GitHub answered: reporting this as offline would send the owner
        # looking at their network.
        if "couldn't find remote ref" in err:
            raise _Refusal(f"GitHub no longer has the branch this copy follows ({branch}), so it "
                           "can't update itself. Install it again from the install guide.", detail=err)
        raise _Refusal("Cannot reach GitHub", offline=True, detail=err)

    def _status(self, upstream: str) -> dict[str, Any]:
        _, head, _ = self._git("rev-parse", "HEAD")
        _, remote_head, _ = self._git("rev-parse", upstream)
        code, counts, _ = self._git("rev-list", "--left-right", "--count", f"HEAD...{upstream}")
        ahead, behind = (int(n) for n in counts.split()) if code == 0 and counts else (0, 0)
        return {"head": head, "remote": remote_head, "ahead": ahead, "behind": behind}

    # -- U4: edited files ---------------------------------------------------------

    def _edited(self) -> list[str]:
        """Tracked files that differ from HEAD (modified, deleted or staged),
        as Git names them. Untracked files are not Studio's."""

        code, out, err = self._git("status", "--porcelain=v1", "-z", "--untracked-files=no", raw=True)
        if code != 0:
            raise _Refusal("Git couldn't list Studio's files.", detail=err)
        paths: list[str] = []
        fields = out.split("\0")
        index = 0
        while index < len(fields):
            entry = fields[index]
            index += 1
            if len(entry) < 4:
                continue
            paths.append(entry[3:])
            if entry[0] in "RC":
                # A rename's source follows as its own field.
                paths.append(fields[index])
                index += 1
        return sorted(set(path for path in paths if path))

    def _changed_by(self, upstream: str) -> set[str]:
        """Every path the update changes, a rename's both ends included."""

        code, out, err = self._git("diff", "--name-only", "--no-renames", "-z", "HEAD", upstream,
                                   raw=True)
        if code != 0:
            raise _Refusal("Git couldn't compare this copy with the update.", detail=err)
        return {path for path in out.split("\0") if path}

    def _clashes(self, upstream: str) -> list[str]:
        """Edited files the update would overwrite. Edits elsewhere survive a
        fast-forward untouched, so they are nobody's concern."""

        changed = self._changed_by(upstream)
        return [path for path in self._edited() if path in changed]

    def _file(self, path: str) -> Path:
        return self._root / Path(*path.split("/"))

    def _back_up(self, paths: list[str]) -> tuple[str, list[str]]:
        """Copy the owner's versions into `update-backups/<date_time>/`.
        Returns that folder, relative to the Studio folder, and the files
        copied. A file the owner deleted has nothing to keep."""

        stamp = time.strftime("%Y-%m-%d_%H%M%S")
        folder = self._root / BACKUP_FOLDER / stamp
        suffix = 1
        while folder.exists():
            suffix += 1
            folder = self._root / BACKUP_FOLDER / f"{stamp}-{suffix}"
        copied: list[str] = []
        for path in paths:
            source = self._file(path)
            if source.is_file():
                target = folder / Path(*path.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                copied.append(path)
        return (folder.relative_to(self._root).as_posix() if copied else ""), copied

    def _hold(self, paths: list[str]) -> dict[str, bytes | None]:
        """The owner's bytes for each file; None where they deleted it."""

        held: dict[str, bytes | None] = {}
        for path in paths:
            source = self._file(path)
            held[path] = source.read_bytes() if source.is_file() else None
        return held

    def _put_back(self, held: dict[str, bytes | None]) -> None:
        for path, data in held.items():
            target = self._file(path)
            if data is None:
                if target.is_file():
                    target.unlink()
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

    # -- routes -----------------------------------------------------------------

    def check(self) -> dict[str, Any]:
        """`/studio/api/check-update`, in the Extension's reply shape."""

        try:
            remote, branch, upstream = self._follow_release_branch(*self._tracked())
            self._fetch(remote, branch)
            state = self._status(upstream)
        except _Refusal as refusal:
            logger.warning("UPDATE check refused: %s%s", refusal.message,
                           f" ({refusal.detail})" if refusal.detail else "")
            return refusal.reply()
        current = state["head"][:8]
        if state["behind"] == 0:
            logger.info("UPDATE check: up to date at %s (%s)", current, branch)
            return {"update_available": False, "current_commit": current}
        if state["ahead"]:
            logger.warning("UPDATE check: %s has %d commit(s) of its own; not updating",
                           current, state["ahead"])
            return {"update_available": False, "current_commit": current,
                    "error": "This copy has its own commits, so it can't update itself. "
                             "Update it with Git directly."}
        _, log, _ = self._git("log", "--format=%h %s", "-20", f"HEAD..{upstream}")
        logger.info("UPDATE check: %d new commit(s), %s -> %s (%s)", state["behind"],
                    current, state["remote"][:8], branch)
        return {"update_available": True, "current_commit": current,
                "remote_commit": state["remote"][:8], "commits_behind": state["behind"],
                "changelog": [line for line in log.splitlines() if line.strip()]}

    def status(self) -> dict[str, Any]:
        return dict(self._progress)

    def _phase(self, phase: str, pct: int, message: str = "") -> None:
        self._progress = {"phase": phase, "pct": pct, "message": message}

    def apply(self, edited: str | None = None) -> dict[str, Any]:
        """`/studio/api/update`: fetch, then fast-forward. Synchronous, as the
        page expects; it polls `status()` meanwhile.

        `edited` is the owner's answer when the update changes files they
        edited: "replace" (back theirs up, take the release's) or "keep". With
        no answer, such an update stops before touching anything and names
        the files, and the page asks.
        """

        if not self._lock.acquire(blocking=False):
            return {"ok": False, "error": "An update is already running."}
        try:
            self._phase("checking", 5, "Checking update")
            remote, branch, upstream = self._follow_release_branch(*self._tracked())
            self._phase("downloading", 10, "Downloading")
            self._fetch(remote, branch)
            state = self._status(upstream)
            if state["behind"] == 0:
                self._phase("idle", 0)
                return {"ok": False, "error": "Already up to date."}
            if state["ahead"]:
                raise _Refusal("This copy has its own commits, so it can't update itself. "
                               "Update it with Git directly.")
            clashes = self._clashes(upstream)
            if clashes and edited not in (REPLACE, KEEP):
                self._phase("idle", 0)
                logger.info("UPDATE waiting for the owner: the update changes edited file(s) %s",
                            ", ".join(clashes))
                return {"ok": False, "needs_choice": True, "files": clashes,
                        "error": "This update changes files you edited: " + ", ".join(clashes)}
            return self._fast_forward(upstream, state, clashes, edited)
        except _Refusal as refusal:
            self._phase("error", 0, refusal.message)
            logger.warning("UPDATE refused: %s%s", refusal.message,
                           f" ({refusal.detail})" if refusal.detail else "")
            return {"ok": False, "error": refusal.message + (f" ({refusal.detail})" if refusal.detail else "")}
        finally:
            self._lock.release()

    def _fast_forward(self, upstream: str, state: dict[str, Any], clashes: list[str],
                      edited: str | None) -> dict[str, Any]:
        """Restore the clashing files, fast-forward, then keep or back up the
        owner's versions. Their bytes are held throughout, so an update that
        fails puts them back exactly as they were."""

        self._phase("copying", 60, "Copying files")
        held = self._hold(clashes)
        backup, backed_up = "", []
        if clashes and edited == REPLACE:
            backup, backed_up = self._back_up(clashes)
        if clashes:
            code, _, err = self._git("checkout", "HEAD", "--", *clashes)
            if code != 0:
                self._put_back(held)
                raise _Refusal("Git couldn't set your edited files aside.", detail=err)
        code, _, err = self._git("merge", "--ff-only", "--quiet", upstream, timeout=_FETCH_TIMEOUT)
        if code != 0:
            self._put_back(held)
            raise _Refusal("Git could not apply the update.", detail=err)
        kept: list[str] = []
        if clashes and edited == KEEP:
            self._put_back(held)
            kept = clashes
        self._phase("finishing", 95, "Finishing")
        _, head, _ = self._git("rev-parse", "HEAD")
        self._phase("restart", 100, "Restart required")
        logger.info("UPDATE applied: %s -> %s (%d commit(s))%s%s", state["head"][:8], head[:8],
                    state["behind"],
                    f"; backed up {', '.join(backed_up)} to {backup}" if backed_up else "",
                    f"; kept the owner's {', '.join(kept)}" if kept else "")
        reply: dict[str, Any] = {
            "ok": True, "restart_required": True, "new_commit": head[:8] or "latest",
            "message": "Updated. Close Studio and run Start-Studio.bat again to "
                       "finish; refresh the browser afterwards."}
        if backed_up:
            reply["backed_up"] = backed_up
            reply["backup_folder"] = backup
        if clashes and edited == REPLACE:
            # Files the owner deleted: restored, with nothing to back up.
            reply["replaced"] = clashes
        if kept:
            reply["kept"] = kept
        return reply


class _Refusal(Exception):
    def __init__(self, message: str, *, offline: bool = False, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.offline = offline
        # Git's own first line, which names no path the owner did not type.
        self.detail = (detail or "").splitlines()[0][:200] if detail else ""

    def reply(self) -> dict[str, Any]:
        out: dict[str, Any] = {"update_available": False, "error": self.message}
        if self.offline:
            out["offline"] = True
        return out
