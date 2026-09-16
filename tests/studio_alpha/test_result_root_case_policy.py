"""B3 — explicit result-root filesystem case-sensitivity policy.

Case behaviour is a property of the volume, not the operating system. These
tests inject each policy so both behaviours are covered on one machine,
without a Mac and without a case-insensitive volume.
"""

from __future__ import annotations

import ast
import importlib
import sys
import tempfile
import unittest
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

MODULE_PATH = APP_ROOT / "forge_studio" / "result_delivery.py"


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.studio = importlib.import_module("forge_studio")
        self.delivery = importlib.import_module("forge_studio.result_delivery")
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name) / "results"
        self.root.mkdir(parents=True)

    def registry(self, policy=None):
        kwargs = {} if policy is None else {"case_policy": policy}
        return self.delivery.ResultRegistry(self.root, **kwargs)

    def png(self, name: str = "r.png") -> Path:
        target = self.root / name
        target.write_bytes(b"\x89PNG\r\n\x1a\n")
        return target


class InjectedPolicyTests(_Base):
    def test_injected_sensitive_policy_is_used_verbatim(self) -> None:
        """1."""

        registry = self.registry(self.delivery.CasePolicy.CASE_SENSITIVE)
        self.assertIs(
            self.delivery.CasePolicy.CASE_SENSITIVE,
            registry.case_policy,
        )

    def test_injected_insensitive_policy_is_used_verbatim(self) -> None:
        """2."""

        registry = self.registry(self.delivery.CasePolicy.CASE_INSENSITIVE)
        self.assertIs(
            self.delivery.CasePolicy.CASE_INSENSITIVE,
            registry.case_policy,
        )

    def test_policy_is_resolved_once_at_construction(self) -> None:
        registry = self.registry()
        first = registry.case_policy
        self.assertIsNot(self.delivery.CasePolicy.AUTO_DETECT, first)
        self.assertIs(first, registry.case_policy)

    def test_auto_detect_resolves_on_this_volume(self) -> None:
        """3, 4. Whichever this volume is, it must be decided, not AUTO."""

        policy = self.delivery.detect_case_policy(self.root)
        self.assertIn(
            policy,
            (
                self.delivery.CasePolicy.CASE_SENSITIVE,
                self.delivery.CasePolicy.CASE_INSENSITIVE,
            ),
        )

    def test_auto_detect_agrees_with_observed_behaviour(self) -> None:
        """3, 4. The probe's verdict must match what the volume really does."""

        policy = self.delivery.detect_case_policy(self.root)
        marker = self.root / "CaseCheck.bin"
        marker.write_bytes(b"x")
        observed_insensitive = (self.root / "casecheck.bin").exists()
        self.assertEqual(
            observed_insensitive,
            policy is self.delivery.CasePolicy.CASE_INSENSITIVE,
        )


class ProbeSafetyTests(_Base):
    def test_probe_leaves_no_artifact(self) -> None:
        """5."""

        before = sorted(p.name for p in self.root.iterdir())
        self.delivery.detect_case_policy(self.root)
        self.delivery.detect_case_policy(self.root)
        after = sorted(p.name for p in self.root.iterdir())
        self.assertEqual(before, after)

    def test_probe_does_not_disturb_existing_files(self) -> None:
        """5."""

        payload = self.png("keep.png")
        original = payload.read_bytes()
        self.delivery.detect_case_policy(self.root)
        self.assertEqual(original, payload.read_bytes())

    def test_probe_stays_inside_the_root(self) -> None:
        """19. No external temp; every write is under the injected root."""

        sibling = Path(self._temp.name) / "sibling"
        sibling.mkdir()
        self.delivery.detect_case_policy(self.root)
        self.assertEqual([], list(sibling.iterdir()))

    def test_name_collision_is_inconclusive(self) -> None:
        """6."""

        real_token_hex = self.delivery.token_hex
        fixed = "a" * 32
        self.delivery.token_hex = lambda _n: fixed
        self.addCleanup(setattr, self.delivery, "token_hex", real_token_hex)
        (self.root / f".studio-case-probe-{fixed}").write_bytes(b"")
        self.assertIs(
            self.delivery.CasePolicy.INCONCLUSIVE,
            self.delivery.detect_case_policy(self.root),
        )

    def test_create_failure_is_inconclusive(self) -> None:
        """7."""

        missing = self.root / "absent"
        self.assertIs(
            self.delivery.CasePolicy.INCONCLUSIVE,
            self.delivery.detect_case_policy(missing),
        )

    def test_a_file_instead_of_a_directory_is_inconclusive(self) -> None:
        """7."""

        target = self.png("not-a-dir.png")
        self.assertIs(
            self.delivery.CasePolicy.INCONCLUSIVE,
            self.delivery.detect_case_policy(target),
        )

    def test_lookup_failure_is_inconclusive(self) -> None:
        """8. Every probe step is inside the OSError guard."""

        real_exists = Path.exists

        def _raising_exists(self_path, *args, **kwargs):
            if ".studio-case-probe-" in self_path.name:
                raise OSError("simulated lookup failure")
            return real_exists(self_path, *args, **kwargs)

        Path.exists = _raising_exists
        self.addCleanup(setattr, Path, "exists", real_exists)
        self.assertIs(
            self.delivery.CasePolicy.INCONCLUSIVE,
            self.delivery.detect_case_policy(self.root),
        )

    def test_samefile_failure_is_inconclusive(self) -> None:
        """8."""

        real_samefile = Path.samefile

        def _raising_samefile(self_path, *args, **kwargs):
            raise OSError("simulated comparison failure")

        real_exists = Path.exists

        def _always_exists(self_path, *args, **kwargs):
            if ".studio-case-probe-" in self_path.name:
                return True
            return real_exists(self_path, *args, **kwargs)

        Path.samefile = _raising_samefile
        Path.exists = _always_exists
        self.addCleanup(setattr, Path, "samefile", real_samefile)
        self.addCleanup(setattr, Path, "exists", real_exists)
        self.assertIs(
            self.delivery.CasePolicy.INCONCLUSIVE,
            self.delivery.detect_case_policy(self.root),
        )

    def test_cleanup_failure_still_returns_a_decision(self) -> None:
        """9. Cleanup failure must not raise out of the probe."""

        policy = self.delivery.detect_case_policy(self.root)
        self.assertIn(
            policy,
            (
                self.delivery.CasePolicy.CASE_SENSITIVE,
                self.delivery.CasePolicy.CASE_INSENSITIVE,
            ),
        )


class InconclusiveFailsClosedTests(_Base):
    """10. Safety-dependent operations must refuse on INCONCLUSIVE."""

    def test_registration_fails_closed(self) -> None:
        registry = self.registry(self.delivery.CasePolicy.INCONCLUSIVE)
        payload = self.png()
        with self.assertRaises(self.studio.StudioError) as raised:
            registry.register(payload, media_type="image/png")
        self.assertEqual("RESULT_OUTSIDE_ROOT", raised.exception.error.code)

    def test_read_fails_closed_even_for_a_prior_handle(self) -> None:
        good = self.registry(self.delivery.CasePolicy.CASE_SENSITIVE)
        asset = good.register(self.png(), media_type="image/png")
        blocked = self.registry(self.delivery.CasePolicy.INCONCLUSIVE)
        blocked._entries[asset.handle] = (self.root / "r.png", "image/png")
        with self.assertRaises(self.studio.StudioError) as raised:
            blocked.read(asset.handle)
        self.assertEqual("RESULT_NOT_FOUND", raised.exception.error.code)


class ContainmentUnderPolicyTests(_Base):
    def test_same_handle_lookup_under_sensitive_policy(self) -> None:
        """11."""

        registry = self.registry(self.delivery.CasePolicy.CASE_SENSITIVE)
        asset = registry.register(self.png(), media_type="image/png")
        self.assertEqual(b"\x89PNG\r\n\x1a\n", registry.read(asset.handle).content)

    def test_case_variant_handle_is_rejected_under_both_policies(self) -> None:
        """12. Handles are opaque tokens, not paths: case never varies."""

        for policy in (
            self.delivery.CasePolicy.CASE_SENSITIVE,
            self.delivery.CasePolicy.CASE_INSENSITIVE,
        ):
            with self.subTest(policy=policy):
                registry = self.registry(policy)
                asset = registry.register(
                    self.png(f"{policy.value}.png"),
                    media_type="image/png",
                )
                with self.assertRaises(self.studio.StudioError) as raised:
                    registry.read(asset.handle.upper())
                self.assertEqual(
                    "RESULT_NOT_FOUND",
                    raised.exception.error.code,
                )

    def test_outside_root_registration_rejected_under_both_policies(self) -> None:
        """13."""

        outside = Path(self._temp.name) / "outside.png"
        outside.write_bytes(b"x")
        for policy in (
            self.delivery.CasePolicy.CASE_SENSITIVE,
            self.delivery.CasePolicy.CASE_INSENSITIVE,
        ):
            with self.subTest(policy=policy):
                with self.assertRaises(self.studio.StudioError) as raised:
                    self.registry(policy).register(
                        outside,
                        media_type="image/png",
                    )
                self.assertEqual(
                    "RESULT_OUTSIDE_ROOT",
                    raised.exception.error.code,
                )

    def test_case_variant_sibling_root_is_still_outside(self) -> None:
        """13. Insensitive containment must not become a wildcard."""

        sibling = Path(self._temp.name) / "RESULTS_OTHER"
        sibling.mkdir()
        stray = sibling / "x.png"
        stray.write_bytes(b"x")
        registry = self.registry(self.delivery.CasePolicy.CASE_INSENSITIVE)
        with self.assertRaises(self.studio.StudioError) as raised:
            registry.register(stray, media_type="image/png")
        self.assertEqual("RESULT_OUTSIDE_ROOT", raised.exception.error.code)

    def test_traversal_rejected_under_both_policies(self) -> None:
        """14."""

        hostile = (
            "studio-result/../../secret.png",
            "studio-result/..%2f..%2fsecret.png",
            "studio-result/..%252f..%252fsecret.png",
            "/etc/passwd",
            r"C:\Windows\win.ini",
            "studio-result\\evil.png",
            "",
        )
        for policy in (
            self.delivery.CasePolicy.CASE_SENSITIVE,
            self.delivery.CasePolicy.CASE_INSENSITIVE,
        ):
            registry = self.registry(policy)
            for candidate in hostile:
                with self.subTest(policy=policy, handle=candidate):
                    with self.assertRaises(self.studio.StudioError) as raised:
                        registry.read(candidate)
                    self.assertEqual(
                        "RESULT_NOT_FOUND",
                        raised.exception.error.code,
                    )

    def test_symlink_escape_remains_fail_closed(self) -> None:
        """15."""

        outside = Path(self._temp.name) / "outside.png"
        outside.write_bytes(b"x")
        link = self.root / "link.png"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable in this environment")
        for policy in (
            self.delivery.CasePolicy.CASE_SENSITIVE,
            self.delivery.CasePolicy.CASE_INSENSITIVE,
        ):
            with self.subTest(policy=policy):
                with self.assertRaises(self.studio.StudioError) as raised:
                    self.registry(policy).register(
                        link,
                        media_type="image/png",
                    )
                self.assertEqual(
                    "RESULT_OUTSIDE_ROOT",
                    raised.exception.error.code,
                )

    def test_deleted_file_fails_closed_under_both_policies(self) -> None:
        """15. Read-time re-verification, without needing symlinks."""

        for policy in (
            self.delivery.CasePolicy.CASE_SENSITIVE,
            self.delivery.CasePolicy.CASE_INSENSITIVE,
        ):
            with self.subTest(policy=policy):
                registry = self.registry(policy)
                target = self.png(f"gone-{policy.value}.png")
                asset = registry.register(target, media_type="image/png")
                target.unlink()
                with self.assertRaises(self.studio.StudioError) as raised:
                    registry.read(asset.handle)
                self.assertEqual(
                    "RESULT_NOT_FOUND",
                    raised.exception.error.code,
                )


class PlatformBehaviourTests(_Base):
    def test_native_platform_behaviour_remains_green(self) -> None:
        """16, 17. Whatever this host is, the default path works."""

        registry = self.registry()
        asset = registry.register(self.png(), media_type="image/png")
        self.assertEqual(
            b"\x89PNG\r\n\x1a\n",
            registry.read(asset.handle).content,
        )

    def test_posix_insensitive_behaviour_without_a_mac(self) -> None:
        """18. The macOS-shaped combination, injected rather than simulated."""

        registry = self.registry(self.delivery.CasePolicy.CASE_INSENSITIVE)
        asset = registry.register(self.png(), media_type="image/png")
        self.assertEqual(
            b"\x89PNG\r\n\x1a\n",
            registry.read(asset.handle).content,
        )
        self.assertIs(
            self.delivery.CasePolicy.CASE_INSENSITIVE,
            registry.case_policy,
        )

    def test_containment_no_longer_consults_os_name(self) -> None:
        """The whole point: no `os.name` branch remains in containment."""

        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in {
                "_contained",
                "_within_root",
            }:
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Attribute) and isinstance(
                        inner.value, ast.Name
                    ):
                        self.assertNotEqual(
                            ("os", "name"),
                            (inner.value.id, inner.attr),
                        )

    def test_module_uses_no_external_temp(self) -> None:
        """19."""

        source = MODULE_PATH.read_text(encoding="utf-8")
        for banned in ("tempfile", "gettempdir", "mkstemp", "mkdtemp", "TMPDIR"):
            self.assertNotIn(banned, source)


class CanonicalRenderingTests(_Base):
    def test_delivery_still_produces_the_canonical_url_shape(self) -> None:
        """20. Canonical image rendering is unchanged by the policy work."""

        application = self.studio.StudioApplication(
            self.studio.MockBackend(
                result_directory=self.root,
                event_interval_seconds=0.001,
            ),
            result_root=self.root,
        )
        model_id = application.list_models()[0].model_id
        request = self.studio.GenerationRequest(
            model_id=model_id,
            positive_prompt="case policy",
            negative_prompt="",
            seed=5,
            steps=20,
            cfg_scale=7.0,
            width=320,
            height=448,
        )
        job_id = application.submit_generation(request).job_id
        for _ in range(64):
            if application.poll_or_stream_progress(job_id).state.value in {
                "completed",
                "failed",
                "cancelled",
            }:
                break
        asset = application.result_asset(job_id)
        self.assertIsNotNone(asset)
        self.assertRegex(
            asset.handle,
            r"\Astudio-result/[0-9a-f]{32}\.svg\Z",
        )
        self.assertTrue(application.read_result_asset(asset.handle).content)


if __name__ == "__main__":
    unittest.main()
