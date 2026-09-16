"""Path identity: containment, case behaviour, name limits, normalization.

These moved out of `catalogue.py` in P0.4b because the directory browser became
a second consumer, and two implementations of "is this path inside that one" is
how the two drift until one is wrong in a way nobody notices.

Three of them were also wrong, in ways a single-OS test suite had no way to
see. Each fix is tested here against INJECTED operating-system answers rather
than against whatever this host happens to do, because the defects are on
platforms this box is not:

```text
detect_case_policy_readonly   one odd entry decided the whole verdict
name_is_acceptable            counted characters; the limits are bytes on
                              Linux and UTF-16 code units on Windows
nfc                           one macOS file, two ids
```

Fail-closed behaviour is asserted explicitly, because "fixed the false verdict"
and "stopped refusing" are easy to confuse and only one of them was wanted.

SCOPE: MINIMAL_RUNTIME_SCOPE. Real directories are created under a temporary
root; nothing is opened, loaded or served.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_headless.catalogue import _model_id  # noqa: E402
from forge_headless.path_identity import (  # noqa: E402
    MAX_CASE_PROBE_CANDIDATES,
    MAX_NAME_LENGTH,
    CasePolicy,
    canonical_spelling,
    detect_case_policy_readonly,
    has_entries,
    is_prefix,
    name_is_acceptable,
    nfc,
)

#: Asserted against the discovered count so a silently dropped test fails.
EXPECTED_PATH_IDENTITY_TESTS = 36


class _Patches:
    """Start a set of patches together, stop them together."""

    def __init__(self, *patches) -> None:
        self._patches = patches

    def __enter__(self) -> "_Patches":
        for patch in self._patches:
            patch.start()
        return self

    def __exit__(self, *_exception) -> None:
        for patch in reversed(self._patches):
            patch.stop()


class _TempRootCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="path-identity-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)


# --------------------------------------------------------------------------
# 1. The case probe: fixed without loosening the refusal
# --------------------------------------------------------------------------


class CaseProbeTests(_TempRootCase):
    def test_a_real_directory_is_decided(self) -> None:
        """On this host, whatever it is, the probe must reach a verdict."""

        (self.root / "Alpha.safetensors").write_bytes(b"")
        policy = detect_case_policy_readonly(self.root)
        self.assertIn(
            policy, (CasePolicy.CASE_SENSITIVE, CasePolicy.CASE_INSENSITIVE)
        )

    def test_windows_and_macos_answer_insensitive(self) -> None:
        """A volume where the flipped spelling reaches the SAME entry.

        Injected, because a case-sensitive host cannot produce this state and a
        test that skips proves nothing about the platform it skipped for.
        """

        with self._injected(
            names=["Alpha.safetensors"],
            lexists=True,
            identity={"Alpha.safetensors": (1, 42), "aLPHA.SAFETENSORS": (1, 42)},
        ):
            self.assertIs(
                CasePolicy.CASE_INSENSITIVE,
                detect_case_policy_readonly(self.root),
            )

    def test_linux_answers_sensitive_when_the_variant_is_absent(self) -> None:
        with self._injected(
            names=["Alpha.safetensors"], lexists=False, identity={}
        ):
            self.assertIs(
                CasePolicy.CASE_SENSITIVE,
                detect_case_policy_readonly(self.root),
            )

    def test_two_entries_differing_only_by_case_mean_sensitive(self) -> None:
        with self._injected(
            names=["Alpha.bin"],
            lexists=True,
            identity={"Alpha.bin": (1, 1), "aLPHA.BIN": (1, 2)},
        ):
            self.assertIs(
                CasePolicy.CASE_SENSITIVE,
                detect_case_policy_readonly(self.root),
            )

    def test_a_broken_link_no_longer_forces_a_false_sensitive(self) -> None:
        """THE BUG. `Path.exists()` follows links, so a broken link answered
        False and the probe concluded CASE_SENSITIVE on a case-INSENSITIVE
        volume -- from one entry, for the whole directory. `lexists` answers
        the name rather than the target."""

        with self._injected(
            names=["Broken.lnk", "Alpha.safetensors"],
            # The flipped spelling EXISTS as a name -- it is the same
            # case-insensitive entry -- but the link target does not, which is
            # exactly what `exists()` used to answer False for.
            lexists=True,
            identity={
                "Broken.lnk": (1, 7), "bROKEN.LNK": (1, 7),
                "Alpha.safetensors": (1, 42), "aLPHA.SAFETENSORS": (1, 42),
            },
        ):
            self.assertIs(
                CasePolicy.CASE_INSENSITIVE,
                detect_case_policy_readonly(self.root),
            )

    def test_one_unreadable_entry_no_longer_decides_for_the_rest(self) -> None:
        """THE OTHER HALF. An entry whose identity cannot be read used to
        return INCONCLUSIVE -- and a non-empty root with an inconclusive policy
        is REFUSED, so one awkward file made a whole model folder unusable. It
        is skipped now, and the next candidate decides."""

        with self._injected(
            names=["Locked.bin", "Alpha.safetensors"],
            lexists=True,
            identity={
                "Locked.bin": None, "lOCKED.BIN": None,
                "Alpha.safetensors": (1, 42), "aLPHA.SAFETENSORS": (1, 42),
            },
        ):
            self.assertIs(
                CasePolicy.CASE_INSENSITIVE,
                detect_case_policy_readonly(self.root),
            )

    def test_all_candidates_unreadable_still_refuses(self) -> None:
        """Fail-closed is NOT what was relaxed. Running out of candidates
        without a verdict is still INCONCLUSIVE, and INCONCLUSIVE still
        refuses a non-empty root."""

        with self._injected(
            names=["A.bin", "B.bin"],
            lexists=True,
            identity={
                "A.bin": None, "a.BIN": None,
                "B.bin": None, "b.BIN": None,
            },
        ):
            self.assertIs(
                CasePolicy.INCONCLUSIVE, detect_case_policy_readonly(self.root)
            )

    def test_a_directory_of_caseless_names_is_inconclusive(self) -> None:
        # Genuinely caseless THROUGHOUT, extension included. Worth stating
        # because the obvious fixture is wrong: a CJK stem with an ASCII
        # extension looks caseless and is not -- swapcase() reaches the
        # extension and yields ".BIN", so on a case-insensitive volume the
        # probe decides from it. Only a name with no cased character
        # anywhere says nothing about the volume.
        for name in ("1234.567", "中文", "模型.中"):
            (self.root / name).write_bytes(b"")
        self.assertIs(
            CasePolicy.INCONCLUSIVE, detect_case_policy_readonly(self.root)
        )

    def test_an_unlistable_root_is_inconclusive(self) -> None:
        with mock.patch("os.listdir", side_effect=OSError("denied")):
            self.assertIs(
                CasePolicy.INCONCLUSIVE, detect_case_policy_readonly(self.root)
            )

    def test_the_probe_is_bounded(self) -> None:
        """A model folder can hold hundreds of thousands of files. A verdict
        that needs more than a handful of samples is not a verdict."""

        examined: list[str] = []

        def lexists(path):  # type: ignore[no-untyped-def]
            examined.append(Path(path).name)
            return False if len(examined) > 10_000 else True

        names = [f"Name{index}.bin" for index in range(10_000)]
        with mock.patch("os.listdir", return_value=names), mock.patch(
            "os.path.lexists", side_effect=lexists
        ), mock.patch(
            "forge_headless.path_identity._identity", return_value=None
        ):
            detect_case_policy_readonly(self.root)
        self.assertLessEqual(len(examined), MAX_CASE_PROBE_CANDIDATES)

    def _injected(self, *, names, lexists, identity):
        """Present the probe a filesystem that answers as a named platform.

        Deliberately dumb: `lexists` is one boolean or a per-NAME mapping, and
        `identity` is a per-NAME mapping to `(st_dev, st_ino)` or None. No
        derived rules, because a helper that computes what the platform would
        have said is a second implementation of the thing under test.
        """

        def fake_lexists(path):  # type: ignore[no-untyped-def]
            if isinstance(lexists, bool):
                return lexists
            return lexists[Path(path).name]

        def fake_identity(path):  # type: ignore[no-untyped-def]
            return identity[Path(path).name]

        return _Patches(
            mock.patch("os.listdir", return_value=list(names)),
            mock.patch("os.path.lexists", side_effect=fake_lexists),
            mock.patch(
                "forge_headless.path_identity._identity",
                side_effect=fake_identity,
            ),
        )


# --------------------------------------------------------------------------
# 2. Name limits, in every unit that counts
# --------------------------------------------------------------------------


class NameLengthTests(unittest.TestCase):
    def test_an_ordinary_name_is_acceptable(self) -> None:
        self.assertTrue(name_is_acceptable("Hicks_Anima_Beta.safetensors"))

    def test_the_character_limit_still_holds(self) -> None:
        self.assertTrue(name_is_acceptable("a" * MAX_NAME_LENGTH))
        self.assertFalse(name_is_acceptable("a" * (MAX_NAME_LENGTH + 1)))

    def test_a_name_over_the_byte_limit_is_refused(self) -> None:
        """Linux and most POSIX filesystems limit 255 BYTES. 200 three-byte
        characters is 200 under the old check and 600 over the real one."""

        name = "\u4e2d" * 200  # 200 chars, 600 UTF-8 bytes
        self.assertLessEqual(len(name), MAX_NAME_LENGTH)
        self.assertGreater(len(name.encode("utf-8")), MAX_NAME_LENGTH)
        self.assertFalse(name_is_acceptable(name))

    def test_a_name_over_the_utf16_limit_is_refused(self) -> None:
        """Windows and NTFS limit 255 UTF-16 CODE UNITS. An astral-plane
        character is one Python character and two code units."""

        name = "\U0001f600" * 200  # 200 chars, 400 UTF-16 code units
        self.assertLessEqual(len(name), MAX_NAME_LENGTH)
        self.assertGreater(len(name.encode("utf-16-le")) // 2, MAX_NAME_LENGTH)
        self.assertFalse(name_is_acceptable(name))

    def test_a_lone_surrogate_does_not_raise(self) -> None:
        """Names arrive from the filesystem, not from a validator."""

        self.assertTrue(name_is_acceptable("a\udcffb.safetensors"))


# --------------------------------------------------------------------------
# 3. Normalization and identity
# --------------------------------------------------------------------------


class NormalizationTests(unittest.TestCase):
    COMPOSED = "caf\u00e9.safetensors"
    DECOMPOSED = "cafe\u0301.safetensors"

    def test_the_two_spellings_really_are_different_strings(self) -> None:
        self.assertNotEqual(self.COMPOSED, self.DECOMPOSED)
        self.assertEqual(nfc(self.COMPOSED), nfc(self.DECOMPOSED))

    def test_one_macos_file_now_has_one_id(self) -> None:
        """THE BUG. HFS+ hands back decomposed names and APFS compares
        normalization-insensitively, so the same file arrived one way from a
        scan and another from a typed path -- and got two ids."""

        self.assertEqual(
            _model_id("checkpoint", "/models", self.COMPOSED),
            _model_id("checkpoint", "/models", self.DECOMPOSED),
        )

    def test_the_root_half_is_normalized_too(self) -> None:
        self.assertEqual(
            _model_id("checkpoint", "/mod\u00e9ls", "a.safetensors"),
            _model_id("checkpoint", "/mode\u0301ls", "a.safetensors"),
        )

    def test_ascii_ids_do_not_move(self) -> None:
        """The normalization must not be an id migration for anyone whose
        filenames are ASCII, which is every id currently in a config file."""

        self.assertEqual(
            # Pinned. ASCII is NFC-invariant, so this is provably the same
            # value the digest produced before P0.4b normalized its inputs.
            "f0b7fb81c390b369b3b3ae580dbb4563",
            _model_id("checkpoint", "/models", "Hicks_Anima_Beta.safetensors"),
        )

    def test_normalization_does_not_merge_genuinely_different_names(self) -> None:
        self.assertNotEqual(
            _model_id("checkpoint", "/m", "alpha.safetensors"),
            _model_id("checkpoint", "/m", "beta.safetensors"),
        )

    def test_role_still_separates_the_id_spaces(self) -> None:
        self.assertNotEqual(
            _model_id("checkpoint", "/m", self.COMPOSED),
            _model_id("vae", "/m", self.COMPOSED),
        )

    def test_nfc_leaves_ascii_untouched(self) -> None:
        for text in ("plain.safetensors", "C:/Models", "/srv/models"):
            self.assertEqual(text, nfc(text))
            self.assertEqual(text, unicodedata.normalize("NFC", text))


# --------------------------------------------------------------------------
# 4. Containment, unchanged and still under an explicit policy
# --------------------------------------------------------------------------


class ContainmentTests(_TempRootCase):
    def test_a_sibling_prefix_is_not_containment(self) -> None:
        self.assertFalse(
            is_prefix(
                Path("C:/Models"),
                Path("C:/Models-Evil/x"),
                CasePolicy.CASE_SENSITIVE,
            )
        )

    def test_a_child_is_contained(self) -> None:
        self.assertTrue(
            is_prefix(
                Path("C:/Models"),
                Path("C:/Models/x"),
                CasePolicy.CASE_SENSITIVE,
            )
        )

    def test_equality_needs_allow_equal(self) -> None:
        root = Path("C:/Models")
        self.assertFalse(is_prefix(root, root, CasePolicy.CASE_SENSITIVE))
        self.assertTrue(
            is_prefix(root, root, CasePolicy.CASE_SENSITIVE, allow_equal=True)
        )

    def test_a_case_differing_path_is_refused_under_a_sensitive_policy(self) -> None:
        self.assertFalse(
            is_prefix(
                Path("C:/Models"),
                Path("c:/models/x"),
                CasePolicy.CASE_SENSITIVE,
            )
        )

    def test_an_inconclusive_policy_refuses_a_case_differing_path(self) -> None:
        """INCONCLUSIVE must never behave as though it concluded."""

        self.assertFalse(
            is_prefix(
                Path("C:/Models"),
                Path("c:/models/x"),
                CasePolicy.INCONCLUSIVE,
            )
        )

    def test_full_case_folding_is_not_used(self) -> None:
        """`str.casefold()` folds the long s to 's' and the sharp s to 'ss',
        so 'Model\u017f'.casefold() == 'models'. NTFS does neither, so those
        directories coexist -- and a folding comparison declared one to
        contain the other."""

        self.assertFalse(
            is_prefix(
                Path("C:/models"),
                Path("C:/Model\u017f/x"),
                CasePolicy.CASE_INSENSITIVE,
            )
        )


class EmptinessTests(_TempRootCase):
    def test_an_empty_root_reports_empty(self) -> None:
        self.assertFalse(has_entries(self.root))

    def test_a_populated_root_reports_non_empty(self) -> None:
        (self.root / "a.safetensors").write_bytes(b"")
        self.assertTrue(has_entries(self.root))

    def test_an_unlistable_root_counts_as_non_empty(self) -> None:
        """So it is refused rather than waved through as 'empty'."""

        with mock.patch("os.scandir", side_effect=OSError("denied")):
            self.assertTrue(has_entries(self.root))


class CanonicalSpellingTests(_TempRootCase):
    """Tested, deliberately NOT wired. Enabling it changes `_root_key` once for
    POSIX owners whose spelling differs from disk, which silently invalidates a
    remembered selection -- a separate decision from the browser."""

    def test_it_returns_the_on_disk_spelling(self) -> None:
        (self.root / "Models").mkdir()
        typed = self.root / "Models"
        self.assertEqual(typed, canonical_spelling(typed))

    def test_an_unreadable_component_returns_the_input(self) -> None:
        with mock.patch("os.scandir", side_effect=OSError("denied")):
            self.assertEqual(self.root, canonical_spelling(self.root))

    def test_it_is_not_wired_into_the_catalogue(self) -> None:
        source = (
            APP_ROOT / "forge_headless" / "catalogue.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("canonical_spelling", source)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_PATH_IDENTITY_TESTS, loaded.countTestCases())

    def test_the_suite_declares_its_scope(self) -> None:
        self.assertIn("MINIMAL_RUNTIME_SCOPE", __doc__ or "")


if __name__ == "__main__":
    unittest.main()
