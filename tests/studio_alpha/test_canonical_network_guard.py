"""The canonical runner's network guard, tested as a thing in its own right.

The guard decides whether every other suite in this directory is trustworthy,
and it had been guarding the wrong noun: `socket.socket` the CONSTRUCTOR
rather than the calls that reach out. A third-party import-time capability
probe -- construct an AF_INET6 socket, bind an ephemeral loopback port, close
it -- tripped it, so the runner reported a network violation against a suite
that had committed none, and reported it without naming what or where.

These tests hold both halves of the repair:

```text
still refuses   connect, connect_ex, create_connection, listen, accept,
                sendto, getaddrinfo, gethostbyname, urlopen, urlretrieve

now permits     construct, bind an ephemeral loopback port, close
```

The refusals are proven against the real call sites, and one of them is proven
A/B in a subprocess: the SAME call that the guard intercepts reaches the
operating system and is refused by it when the guard is not installed. A guard
tested only against itself proves only that it is self-consistent.

SCOPE: MINIMAL_RUNTIME_SCOPE. Sockets are constructed and bound to ephemeral
loopback ports; nothing connects, listens, serves or resolves.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import unittest
import urllib.request
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

# `run_tests` is a sibling script, not an installed module. Under `run_tests.py`
# this directory is already the discovery root; under `unittest discover -t .`
# it is not, so put it on the path explicitly.
TEST_ROOT = Path(__file__).resolve().parent
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

from run_tests import NetworkGuard  # noqa: E402  (path set above)

#: Discard protocol. Nothing listens there, which is the point: a real dial
#: gets a refusal from the OS rather than a connection to anything.
CLOSED_PORT = ("127.0.0.1", 9)

#: Asserted against the discovered count so a silently dropped test fails.
EXPECTED_GUARD_TESTS = 23


class _GuardCase(unittest.TestCase):
    """Installs its own guard, nested inside the runner's when there is one.

    Nesting is deliberate: refusals raised in here land in THIS guard's ledger
    and are restored on exit, so proving the guard works cannot itself look
    like a violation to the run that is proving it.
    """

    def setUp(self) -> None:
        self.guard = NetworkGuard()
        self.guard.__enter__()
        self.addCleanup(self.guard.__exit__)

    def refuses(self, call, *args) -> str:  # type: ignore[no-untyped-def]
        before = len(self.guard.attempts)
        with self.assertRaises(AssertionError) as caught:
            call(*args)
        self.assertGreater(
            len(self.guard.attempts), before, "the attempt was not recorded"
        )
        return str(caught.exception)


# --------------------------------------------------------------------------
# 1. What must still be refused
# --------------------------------------------------------------------------


class RefusalTests(_GuardCase):
    def test_connect_is_refused(self) -> None:
        with socket.socket() as sock:
            self.assertIn("connect", self.refuses(sock.connect, CLOSED_PORT))

    def test_connect_ex_is_refused(self) -> None:
        """Refused, not merely reported. `connect_ex` returns an error code
        instead of raising, so a guard that only patched `connect` would let a
        polite caller dial out and read the result from the return value."""

        with socket.socket() as sock:
            self.refuses(sock.connect_ex, CLOSED_PORT)

    def test_create_connection_is_refused(self) -> None:
        self.refuses(socket.create_connection, CLOSED_PORT)

    def test_listen_is_refused(self) -> None:
        """`listen` is where a bound socket becomes a server, and serving is
        what the subprocess-driven suites exist to keep out of this process."""

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.refuses(sock.listen)

    def test_accept_is_refused(self) -> None:
        with socket.socket() as sock:
            self.refuses(sock.accept)

    def test_sendto_is_refused(self) -> None:
        """UDP needs no connection, so a connect-only guard would miss it."""

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            self.refuses(sock.sendto, b"x", CLOSED_PORT)

    def test_name_resolution_is_refused(self) -> None:
        """Resolving is reaching. A DNS lookup leaves the process even when
        nothing is dialled afterwards."""

        self.refuses(socket.getaddrinfo, "example.invalid", 80)
        self.refuses(socket.gethostbyname, "example.invalid")

    def test_urlopen_is_refused(self) -> None:
        self.refuses(urllib.request.urlopen, "http://127.0.0.1:9/")

    def test_urlretrieve_is_refused(self) -> None:
        self.refuses(urllib.request.urlretrieve, "http://127.0.0.1:9/")

    def test_every_refusal_names_the_call(self) -> None:
        with socket.socket() as sock:
            self.refuses(sock.connect, CLOSED_PORT)
        self.refuses(socket.create_connection, CLOSED_PORT)
        names = [name for name, _origin in self.guard.attempts]
        self.assertIn("socket.connect", names)
        self.assertIn("socket.create_connection", names)

    def test_the_report_names_what_and_where(self) -> None:
        """The original guard said only that SOMETHING happened. Diagnosing a
        single false positive then cost a full re-run of 1888 tests under a
        stack trace."""

        with socket.socket() as sock:
            self.refuses(sock.connect, CLOSED_PORT)
        report = self.guard.report()
        self.assertIn("socket.connect", report)
        self.assertIn("test_canonical_network_guard.py", report)


# --------------------------------------------------------------------------
# 2. What must now be permitted
# --------------------------------------------------------------------------


class PermissionTests(_GuardCase):
    def test_constructing_a_socket_is_not_a_network_operation(self) -> None:
        socket.socket().close()
        self.assertFalse(self.guard.attempted)

    def test_the_urllib3_ipv6_probe_passes_cleanly(self) -> None:
        """The exact false positive, reproduced verbatim from
        `urllib3/util/connection.py::_has_ipv6`. This is the sequence that
        failed every canonical run that imported transformers."""

        if not socket.has_ipv6:
            self.skipTest("no IPv6 support on this host")
        sock = socket.socket(socket.AF_INET6)
        try:
            sock.bind(("::1", 0))
        except OSError:
            self.skipTest("::1 is not bindable on this host")
        finally:
            sock.close()
        self.assertFalse(self.guard.attempted)

    def test_an_ephemeral_bind_reaches_nothing(self) -> None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.assertGreater(port, 0)
        self.assertFalse(self.guard.attempted)

    def test_a_clean_run_reports_no_attempt(self) -> None:
        self.assertFalse(self.guard.attempted)
        self.assertEqual([], self.guard.attempts)


# --------------------------------------------------------------------------
# 3. A/B against the operating system
# --------------------------------------------------------------------------


_AB_PROBE = (
    "import json, socket, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "sys.path.insert(0, sys.argv[2])\n"
    "from run_tests import NetworkGuard\n"
    "target = ('127.0.0.1', 9)\n"
    "\n"
    "def attempt():\n"
    "    sock = socket.socket()\n"
    "    sock.settimeout(2.0)\n"
    "    try:\n"
    "        sock.connect(target)\n"
    "        return 'connected'\n"
    "    except AssertionError as exc:\n"
    "        return 'guard:' + str(exc)\n"
    "    except OSError as exc:\n"
    "        return 'os:' + type(exc).__name__\n"
    "    finally:\n"
    "        sock.close()\n"
    "\n"
    "unguarded = attempt()\n"
    "with NetworkGuard() as guard:\n"
    "    guarded = attempt()\n"
    "    recorded = [name for name, _origin in guard.attempts]\n"
    "restored = attempt()\n"
    "print(json.dumps({'unguarded': unguarded, 'guarded': guarded,\n"
    "                  'recorded': recorded, 'restored': restored}))\n"
)


class AgainstTheOperatingSystemTests(unittest.TestCase):
    """Proof that the intercepted call is the one that really dials.

    Run in a subprocess, because the unguarded half genuinely reaches the
    operating system and the canonical runner's own guard is active in this
    process -- the same reason every other suite here that needs a real socket
    uses a subprocess.
    """

    def probe(self) -> dict:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [sys.executable, "-I", "-S", "-B", "-c", _AB_PROBE,
             str(APP_ROOT), str(TEST_ROOT)],
            cwd=str(APP_ROOT), capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(0, completed.returncode, completed.stderr[-2000:])
        return json.loads(completed.stdout.strip().splitlines()[-1])

    def test_the_same_call_reaches_the_os_when_unguarded(self) -> None:
        """Without the guard, `connect` to a closed loopback port is refused
        BY THE OPERATING SYSTEM. That is what makes the guarded result mean
        something: the call site under test is a real dial, not a stub."""

        report = self.probe()
        self.assertTrue(
            report["unguarded"].startswith("os:"),
            f"expected an OS-level refusal, got {report['unguarded']!r}",
        )

    def test_the_guard_intercepts_that_same_call(self) -> None:
        report = self.probe()
        self.assertTrue(
            report["guarded"].startswith("guard:"),
            f"the guard did not intercept: {report['guarded']!r}",
        )
        self.assertIn("must not perform network operations", report["guarded"])

    def test_the_interception_is_recorded(self) -> None:
        self.assertIn("socket.connect", self.probe()["recorded"])

    def test_the_guard_restores_the_real_call_on_exit(self) -> None:
        """A guard that left the socket module patched would make every later
        suite pass for the wrong reason."""

        report = self.probe()
        self.assertTrue(
            report["restored"].startswith("os:"),
            f"connect was not restored: {report['restored']!r}",
        )


# --------------------------------------------------------------------------
# 4. The runner turns a detection into a failed run
# --------------------------------------------------------------------------


_RUNNER_PROBE = (
    "import json, sys, tempfile\n"
    "from pathlib import Path\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "sys.path.insert(0, sys.argv[2])\n"
    "import run_tests\n"
    "\n"
    "DIALS = '''\n"
    "import socket, unittest\n"
    "class T(unittest.TestCase):\n"
    "    def test_it_dials(self):\n"
    "        try:\n"
    "            socket.create_connection((\"127.0.0.1\", 9), timeout=1).close()\n"
    "        except Exception:\n"
    "            pass\n"
    "'''\n"
    "QUIET = '''\n"
    "import socket, unittest\n"
    "class T(unittest.TestCase):\n"
    "    def test_it_only_binds(self):\n"
    "        s = socket.socket()\n"
    "        s.bind((\"127.0.0.1\", 0))\n"
    "        s.close()\n"
    "'''\n"
    "\n"
    "# Distinct module names: `discover` puts the start directory on the path\n"
    "# and caches by module name, so two fixtures sharing one name in one\n"
    "# process collide on the second run rather than the first.\n"
    "def run(name, source):\n"
    "    directory = Path(tempfile.mkdtemp())\n"
    "    (directory / (name + '.py')).write_text(source, encoding='utf-8')\n"
    "    return run_tests.main(start_directory=directory)\n"
    "\n"
    "print(json.dumps({'dialing': run('test_dialing_fixture', DIALS),\n"
    "                  'quiet': run('test_quiet_fixture', QUIET)}))\n"
)


class RunnerExitCodeTests(unittest.TestCase):
    """End to end: a dial must FAIL THE RUN, and a bind must not.

    The fixture test swallows its own exception, so the run is only red if the
    guard's verdict reaches the exit code independently of whether any test
    reported a failure. That is the exact shape of the bug being guarded
    against -- something reaching the network quietly, inside a try/except,
    while every test still passes.
    """

    def probe(self) -> dict:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [sys.executable, "-I", "-S", "-B", "-c", _RUNNER_PROBE,
             str(APP_ROOT), str(TEST_ROOT)],
            cwd=str(APP_ROOT), capture_output=True, text=True, timeout=180,
        )
        self.assertEqual(0, completed.returncode, completed.stderr[-2000:])
        return json.loads(completed.stdout.strip().splitlines()[-1])

    def test_a_swallowed_dial_still_fails_the_run(self) -> None:
        self.assertEqual(1, self.probe()["dialing"])

    def test_a_bind_only_suite_passes(self) -> None:
        """The regression that started this: a suite that touches sockets
        without reaching anything must return zero."""

        self.assertEqual(0, self.probe()["quiet"])


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_GUARD_TESTS, loaded.countTestCases())

    def test_the_suite_declares_its_scope(self) -> None:
        self.assertIn("MINIMAL_RUNTIME_SCOPE", __doc__ or "")


if __name__ == "__main__":
    unittest.main()
