"""Run the Studio alpha contract tests with only the standard library.

The run is guarded: no test may reach anything outside its own process. The
guard is on the operations that DO the reaching, not on the objects that could
have been used to reach:

```text
refused   connect, connect_ex, create_connection    dialling out
          listen, accept                            serving
          sendto, sendmsg                           connectionless send
          getaddrinfo, gethostbyname                name resolution
          urlopen, urlretrieve                      fetching

allowed   socket() construction
          bind()
          close()
```

`listen()` is the boundary, not `bind()`. A socket that binds an ephemeral
loopback port and closes it has opened nothing, served nothing and talked to
nothing -- and that exact three-step sequence is a capability probe run at
import time by `urllib3.util.connection`:

```python
sock = socket.socket(socket.AF_INET6)
sock.bind(("::1", 0))          # HAS_IPV6 = it worked
sock.close()
```

Guarding the CONSTRUCTOR made that probe fail the whole run, so any suite that
imported `transformers` (through `huggingface_hub` -> `requests` -> `urllib3`)
reported a network violation it had not committed. Worse, it reported it
without saying what or where, so the only way to tell a false positive from a
real one was to re-run everything under a stack trace. Attempts are now
recorded with their call name and origin.

Every property the old guard actually protected is kept: no test starts a
server, and no test talks to anything. Suites that need a real socket still
run it in a subprocess with an unpatched socket module, exactly as before.
"""

from __future__ import annotations

import socket
import sys
import traceback
import unittest
import urllib.request
from pathlib import Path
from typing import Any
from unittest import mock


TEST_DIRECTORY = Path(__file__).resolve().parent
APP_ROOT = TEST_DIRECTORY.parents[1]


class NetworkGuard:
    """Refuses, and records, any attempt to reach outside this process."""

    def __init__(self) -> None:
        self.attempts: list[tuple[str, str]] = []
        self._patchers: list[Any] = []

    def _refuse(self, name: str):  # type: ignore[no-untyped-def]
        def refuse(*_args: object, **_kwargs: object) -> None:
            # Trimmed to the frames between the test and the call: the mock
            # machinery above and the runner below are never the answer.
            origin = "".join(traceback.format_stack()[-8:-4]).strip()
            self.attempts.append((name, origin))
            raise AssertionError(
                f"Studio alpha tests must not perform network operations "
                f"({name})"
            )

        return refuse

    def __enter__(self) -> "NetworkGuard":
        targets = [
            (socket.socket, "connect"),
            (socket.socket, "connect_ex"),
            (socket.socket, "listen"),
            (socket.socket, "accept"),
            (socket.socket, "sendto"),
            # `sendmsg` is absent on Windows. Named by attribute rather than
            # assumed, so the guard is the same guard on every platform it can
            # be and honestly smaller where it cannot.
            (socket.socket, "sendmsg"),
            (socket, "create_connection"),
            (socket, "getaddrinfo"),
            (socket, "gethostbyname"),
            (urllib.request, "urlopen"),
            (urllib.request, "urlretrieve"),
        ]
        for target, name in targets:
            if not hasattr(target, name):
                continue
            label = getattr(target, "__name__", str(target))
            patcher = mock.patch.object(
                target, name, side_effect=self._refuse(f"{label}.{name}")
            )
            patcher.start()
            self._patchers.append(patcher)
        return self

    def __exit__(self, *_exception: object) -> None:
        for patcher in reversed(self._patchers):
            patcher.stop()
        self._patchers.clear()

    @property
    def attempted(self) -> bool:
        return bool(self.attempts)

    def report(self) -> str:
        lines = ["FAILED: a network operation was attempted"]
        seen: set[str] = set()
        for name, origin in self.attempts:
            if origin in seen:
                continue
            seen.add(origin)
            lines.append(f"  {name}")
            for line in origin.splitlines():
                lines.append(f"    {line.strip()}")
        return "\n".join(lines) + "\n"



def main(start_directory: Path | None = None) -> int:
    """Run the contract suite under the guard.

    `start_directory` exists so the runner itself can be tested end to end
    over a fixture: proving the guard refuses a dial is not the same as
    proving THIS FUNCTION turns that refusal into a non-zero exit, and the
    second is what a validator is for.
    """

    sys.path.insert(0, str(APP_ROOT))
    with NetworkGuard() as guard:
        suite = unittest.defaultTestLoader.discover(
            start_dir=str(start_directory or TEST_DIRECTORY),
            pattern="test_*.py",
        )
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    if guard.attempted:
        sys.stderr.write(guard.report())
    return 0 if result.wasSuccessful() and not guard.attempted else 1


if __name__ == "__main__":
    raise SystemExit(main())
