"""Test-only import blocker for `gradio` and `gradio_client`.

Installed as a `sys.meta_path` finder, so it fires **before** any real finder
and therefore catches transitive imports as well as direct ones -- a module
three levels down that imports Gradio raises here, not silently succeeds.

This is test infrastructure. It is never imported by production code, and it
monkeypatches nothing: it adds a finder and removes it again.

`gradio_client` is blocked separately and deliberately. It is a distinct
distribution, but `gradio` pins it exactly and `modules/ui_tempdir.py` uses
three of its helpers, so allowing it would leave a real dependency uncovered.
"""

from __future__ import annotations

import sys
from importlib.abc import MetaPathFinder
from types import ModuleType


FORBIDDEN_ROOTS = ("gradio", "gradio_client")


class ForbiddenImportError(ImportError):
    """Raised the moment a forbidden module is requested."""

    def __init__(self, name: str, importer: str | None) -> None:
        self.forbidden_module = name
        self.importer = importer
        where = f" (imported by {importer})" if importer else ""
        super().__init__(
            f"Forbidden import of {name!r}{where}. The Studio headless path "
            f"must not import Gradio or gradio_client."
        )


def _is_forbidden(name: str) -> bool:
    head = name.split(".", 1)[0]
    return head in FORBIDDEN_ROOTS


class GradioImportBlocker(MetaPathFinder):
    """Refuse `gradio` and `gradio_client`, recording who asked."""

    def __init__(self) -> None:
        self.attempts: list[tuple[str, str | None]] = []

    def find_module(self, fullname, path=None):  # pragma: no cover - legacy API
        self.find_spec(fullname, path)
        return None

    def find_spec(self, fullname, path=None, target=None):
        if not _is_forbidden(fullname):
            return None
        importer = self._importing_module()
        self.attempts.append((fullname, importer))
        raise ForbiddenImportError(fullname, importer)

    @staticmethod
    def _importing_module() -> str | None:
        """Best-effort name of the module that triggered the import.

        Walks out of importlib's own frames to the first real caller. Returns
        None rather than guessing when the stack does not make it clear.
        """

        frame = sys._getframe(1)
        while frame is not None:
            name = frame.f_globals.get("__name__", "")
            filename = frame.f_code.co_filename
            if (
                name
                and not name.startswith("importlib")
                and "importlib" not in filename
                and name != __name__
            ):
                return name
            frame = frame.f_back
        return None


class blocked_gradio_imports:
    """Context manager installing the blocker for the duration of a block.

    Two kinds of `sys.modules` hygiene, both necessary:

    **On entry**, forbidden modules are evicted, so a test cannot pass merely
    because something imported Gradio earlier in the session.

    **On exit**, `sys.modules` is restored to exactly its entry state. A test
    that deliberately imports something to watch it fail -- say
    `modules.shared_gradio_themes` -- would otherwise leave a partially
    initialised `modules` package behind and break later tests that assert
    `forge_studio` pulls in no `modules`. That happened; hence the snapshot.
    """

    def __init__(self) -> None:
        self.blocker = GradioImportBlocker()
        self._snapshot: dict[str, ModuleType] = {}

    def __enter__(self) -> GradioImportBlocker:
        self._snapshot = dict(sys.modules)
        for name in list(sys.modules):
            if _is_forbidden(name):
                del sys.modules[name]
        sys.meta_path.insert(0, self.blocker)
        return self.blocker

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            sys.meta_path.remove(self.blocker)
        except ValueError:
            pass
        # Restore exactly: drop anything the block imported, put back
        # anything it evicted or replaced.
        for name in [n for n in sys.modules if n not in self._snapshot]:
            del sys.modules[name]
        sys.modules.update(self._snapshot)
        self._snapshot.clear()


def forbidden_modules_in_sys_modules() -> list[str]:
    """Any forbidden module currently loaded. Used for post-hoc assertions."""

    return sorted(name for name in sys.modules if _is_forbidden(name))
