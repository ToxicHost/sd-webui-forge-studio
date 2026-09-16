"""Makes `tests` a REGULAR package. Do not delete this file.

It is not a formality and it is not empty by accident.

Without an `__init__.py`, `tests` is a PEP 420 namespace package. Python's
import system resolves a regular package -- any directory with an
`__init__.py`, found anywhere on `sys.path` -- ahead of a namespace package,
*regardless of sys.path order*. `run_tests.py` already does
`sys.path.insert(0, APP_ROOT)`, and that is not enough on its own: position 0
does not beat the package KIND rule.

So a dependency that ships its own top-level `tests` package silently steals
the name. `ultralytics==8.3.119` does exactly that -- it installs its own test
suite to `site-packages/tests/` -- and the moment it was installed, every
`from tests.studio_alpha import ...` in this suite resolved into ULTRALYTICS'
tests instead. Nine modules failed to import and 282 tests stopped running,
while the runner still reported a number and an exit code.

The failure mode is the dangerous kind: a suite that shrinks quietly. The count
went 2240 -> 1958 and nothing said "282 tests are missing" -- they were simply
not collected. It also dragged ultralytics' own conftest into range of the
network guard, which is what actually raised the alarm.

Shipping a top-level `tests` package is a known packaging antipattern, and this
project cannot control which dependency commits it next. This file is the
defence that does not depend on noticing.
"""
