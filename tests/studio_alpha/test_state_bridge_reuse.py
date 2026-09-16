"""One state bridge per process, because the samplers bind it by value.

Reported by the owner: Live Preview worked, then died the moment a different
checkpoint was selected, and stayed dead.

```text
modules/sd_samplers_common.py:29        from modules.shared import opts, state
modules/sd_samplers_cfg_denoiser.py:6   from modules.shared import opts, state
```

That name is resolved ONCE, at first import, and never re-read. So the first
bridge installed in a process is the one `store_latent` writes every latent
into, for the life of that process, whatever `shared.state` is reassigned to
afterwards.

The second load built a second bridge and handed it to the port. The port
re-pointed THAT one at each job, while the sampler kept publishing into the
first -- whose progress object belonged to a closed session. Frames were
decoded into an object nobody reads.

`session_loader` warns about exactly this at its call site:

    a second bridge would leave the sampler reporting into an object
    nobody reads

It turns out to happen ACROSS loads rather than within one.

WHY THE CHECK IS NOT `shared.state`

`close()` restores the original `shared.state` between loads, so by the time
a second load runs, `shared.state` is Neo's own object again while the
samplers still hold the first bridge. Checking `shared.state` would miss every
time and build the second bridge anyway -- which is what the first draft of
this fix did.

SCOPE: MINIMAL_RUNTIME_SCOPE. No engine and no Neo; `modules.*` are stubbed
in `sys.modules` so the by-value binding can be reproduced exactly.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_headless.headless_progress import (  # noqa: E402
    ForgeStateBridge,
    HeadlessProgress,
)
from forge_headless.live_bindings import StudioStartupGlobals  # noqa: E402

#: Asserted against the discovered count so a silently dropped test fails.
EXPECTED_BRIDGE_TESTS = 9


class _Sampler:
    """Stands in for a module that did `from modules.shared import state`."""

    def __init__(self, state: Any) -> None:
        self.state = state


class BridgeReuseTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {
            name: sys.modules.get(name)
            for name in ("modules.sd_samplers_common",
                         "modules.sd_samplers_cfg_denoiser")
        }
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        for name, module in self._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def _bind(self, state: Any) -> None:
        module = types.ModuleType("modules.sd_samplers_common")
        module.state = state
        sys.modules["modules.sd_samplers_common"] = module

    def _unbind(self) -> None:
        sys.modules.pop("modules.sd_samplers_common", None)
        sys.modules.pop("modules.sd_samplers_cfg_denoiser", None)

    def test_a_bridge_the_samplers_hold_is_found(self) -> None:
        bridge = ForgeStateBridge(HeadlessProgress("a"))
        self._bind(bridge)
        self.assertIs(bridge, StudioStartupGlobals._bound_bridge())

    def test_the_cfg_denoiser_binding_counts_too(self) -> None:
        """Both modules bind it; either is authoritative."""

        bridge = ForgeStateBridge(HeadlessProgress("a"))
        self._unbind()
        module = types.ModuleType("modules.sd_samplers_cfg_denoiser")
        module.state = bridge
        sys.modules["modules.sd_samplers_cfg_denoiser"] = module
        self.assertIs(bridge, StudioStartupGlobals._bound_bridge())

    def test_nothing_bound_means_nothing_to_reuse(self) -> None:
        """The discriminating half. A first load MUST build one."""

        self._unbind()
        self.assertIsNone(StudioStartupGlobals._bound_bridge())

    def test_neos_own_state_is_not_mistaken_for_a_bridge(self) -> None:
        """`shared.state` is a real object with a `current_latent`. Adopting
        it would leave the port listening to Neo's own state."""

        self._bind(types.SimpleNamespace(current_latent=None))
        self.assertIsNone(StudioStartupGlobals._bound_bridge())

    def test_a_module_without_state_is_skipped(self) -> None:
        self._unbind()
        sys.modules["modules.sd_samplers_common"] = types.ModuleType(
            "modules.sd_samplers_common"
        )
        self.assertIsNone(StudioStartupGlobals._bound_bridge())


class BridgeIdentityTests(unittest.TestCase):
    """What the reuse is FOR: one object, re-pointed per job."""

    def test_the_bridge_publishes_into_whatever_progress_it_points_at(self) -> None:
        """This is why adopting is safe: the bridge holds no session state,
        so re-pointing it is the whole of switching jobs."""

        first = HeadlessProgress("first")
        bridge = ForgeStateBridge(first)
        second = HeadlessProgress("second")
        object.__setattr__(bridge, "_progress", second)
        bridge.sampling_steps = 8
        bridge.sampling_step = 3
        self.assertEqual(3, second.snapshot().step)
        self.assertEqual(0, first.snapshot().step)

    def test_a_re_pointed_bridge_reports_the_new_jobs_total(self) -> None:
        bridge = ForgeStateBridge(HeadlessProgress("first"))
        second = HeadlessProgress("second")
        object.__setattr__(bridge, "_progress", second)
        bridge.sampling_steps = 15
        self.assertEqual(15, second.snapshot().total_steps)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_BRIDGE_TESTS, loaded.countTestCases())

    def test_the_suite_declares_its_scope(self) -> None:
        self.assertIn("MINIMAL_RUNTIME_SCOPE", __doc__ or "")


if __name__ == "__main__":
    unittest.main()
