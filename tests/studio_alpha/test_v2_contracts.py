"""The Brush V2 / Canvas 2.0 seam contracts. V2-01.

`03_SHARED_BRUSH_CANVAS_CONTRACT.md` §11 asks for one specific thing and it is
the reason most of this file is written the way it is:

    "Current producer/current consumer seam tests enumerate dataclass/schema
     fields so a new field cannot be silently dropped at translation."

ENUMERATED, NOT LISTED. Every test here that checks a set of fields reads it
from `dataclasses.fields`, never from a literal written out by hand. A
hand-written list is exactly what lets a new field be added and silently
dropped: the list still matches itself, and nothing notices the field it does
not mention. So `AdmittedAsset` gaining a field fails the join test until
somebody writes down what happens to it, and `InputAssetRef` gaining one fails
the round trip until it is carried.

WHY THERE ARE NO `NormalizedSample` OR `StrokeDescriptor` TESTS. Those types are
deferred to V2-02 with the reason recorded in
`Evidence/source-review/V2-01-contract-schemas.md` §5: they are the brush
kernel's seam, nothing produces or consumes one until V2-P2.1 builds it, and a
schema written a phase early is a schema written against a guess. The one thing
worth stating now is that `canvas-input.js:102 normalize()` already produces a
near-`NormalizedSample`, and the oracle's `penSample()` is a third spelling of
the same shape -- real drift, and V2-02's first task.

Review: `Evidence/source-review/V2-01-contract-schemas.md`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from dataclasses import fields
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.asset_service import AdmittedAsset  # noqa: E402
from forge_studio.v2_contracts import (  # noqa: E402
    ADMITTED_ASSET_DISPOSITION,
    INPUT_ASSET_REF_UNSOURCED,
    REFUSALS,
    V2_SCHEMA_VERSION,
    GeneratedLayerInfo,
    GenerationSnapshot,
    InputAssetRef,
    Retryable,
    SchemaError,
    TargetKind,
    TargetRef,
)

EXPECTED_V2_CONTRACT_TESTS = 35

HEX32 = "0123456789abcdef" * 2
HEX64 = "0123456789abcdef" * 4
ASSET_ID = f"studio-asset/{HEX32}"

#: Every versioned contract in the module, with a construction that is valid.
#: Read by the round-trip and version tests so a new contract cannot be added
#: without appearing in both.
def _samples():
    ref = InputAssetRef(asset_id=ASSET_ID, sha256=HEX64, media_type="image/png",
                        width=512, height=384, byte_length=1024)
    return [
        TargetRef(kind=TargetKind.LAYER_MASK, document_id=HEX32,
                  layer_id=HEX32, channel_id=HEX32),
        ref,
        GenerationSnapshot(document_id=HEX32, canvas_revision=7,
                           operation="inpaint", source=ref, mask=ref,
                           width=512, height=384, transform_revision=2,
                           included_layer_ids=("a",), excluded_layer_ids=("b",)),
        GeneratedLayerInfo(job_id="studio-job-000001", snapshot_revision=7,
                           result=ref, operation="inpaint", batch_index=1),
    ]


class EveryFieldSurvivesTheRoundTripTests(unittest.TestCase):
    """The join the bundle names, asserted by enumeration."""

    def test_every_declared_field_reaches_the_projection(self) -> None:
        for value in _samples():
            with self.subTest(contract=type(value).__name__):
                payload = value.to_dict()
                for f in fields(value):
                    if f.name == "unknown_fields":
                        # Never projected under its own name -- it is re-emitted
                        # FLAT, which the dedicated test below covers.
                        self.assertNotIn("unknown_fields", payload)
                        continue
                    self.assertIn(
                        f.name, payload,
                        f"{type(value).__name__}.{f.name} does not reach the "
                        "projection, so a consumer reading JSON cannot see it")

    def test_a_round_trip_preserves_every_field(self) -> None:
        for value in _samples():
            with self.subTest(contract=type(value).__name__):
                back = type(value).from_dict(value.to_dict())
                self.assertEqual(value.to_dict(), back.to_dict())

    def test_the_projection_is_json(self) -> None:
        """`to_dict` is a PUBLIC projection. A value json cannot encode is one
        that reaches a browser as a 500."""

        for value in _samples():
            with self.subTest(contract=type(value).__name__):
                json.dumps(value.to_dict())

    def test_an_unknown_field_survives_and_is_re_emitted(self) -> None:
        """The §11 property. An older consumer must not strip a newer
        producer's addition on the way through."""

        payload = dict(_samples()[1].to_dict())
        payload["colour_profile"] = "display-p3"
        back = InputAssetRef.from_dict(payload)
        self.assertEqual({"colour_profile": "display-p3"}, back.unknown_fields)
        self.assertEqual("display-p3", back.to_dict()["colour_profile"])

    def test_an_unknown_field_does_not_collide_with_a_known_one(self) -> None:
        """Re-emitted flat, so a known field must win. Otherwise a stale
        carried value could overwrite the one this version actually holds."""

        value = InputAssetRef(asset_id=ASSET_ID, sha256=HEX64,
                              media_type="image/png", width=1, height=1,
                              byte_length=1,
                              unknown_fields={"width": 9999})
        self.assertEqual(1, value.to_dict()["width"])

    def test_from_dict_refuses_something_that_is_not_a_mapping(self) -> None:
        with self.assertRaises(SchemaError):
            InputAssetRef.from_dict([("asset_id", ASSET_ID)])  # type: ignore[arg-type]


class EveryContractIsVersionedTests(unittest.TestCase):
    def test_each_one_declares_the_current_version(self) -> None:
        for value in _samples():
            with self.subTest(contract=type(value).__name__):
                self.assertEqual(V2_SCHEMA_VERSION, value.schema_version)
                self.assertEqual(V2_SCHEMA_VERSION,
                                 value.to_dict()["schema_version"])

    def test_a_version_arriving_from_a_newer_producer_is_kept(self) -> None:
        """Not clamped, not refused. A consumer that silently rewrote the
        version would report the shape it understood rather than the shape it
        was given."""

        payload = dict(_samples()[0].to_dict())
        payload["schema_version"] = V2_SCHEMA_VERSION + 1
        self.assertEqual(V2_SCHEMA_VERSION + 1,
                         TargetRef.from_dict(payload).schema_version)


class ATargetIsIdentityAndNothingElseTests(unittest.TestCase):
    """"never a path, canvas DOM node, mutable layer object, or base64
    payload". The last three are unrepresentable in a frozen dataclass of
    strings; the path is refused here."""

    PATHS = (
        "/etc/passwd", "..\\..\\secrets", "C:\\Users\\owner\\doc",
        "file:///tmp/x", "http://example.com/a", "layers/3", "a/b",
    )

    def test_a_path_shaped_document_id_is_refused(self) -> None:
        for bad in self.PATHS:
            with self.subTest(value=bad), self.assertRaises(SchemaError):
                TargetRef(document_id=bad, layer_id=HEX32)

    def test_a_path_shaped_layer_id_is_refused(self) -> None:
        for bad in self.PATHS:
            with self.subTest(value=bad), self.assertRaises(SchemaError):
                TargetRef(document_id=HEX32, layer_id=bad)

    def test_a_path_shaped_channel_id_is_refused(self) -> None:
        with self.assertRaises(SchemaError):
            TargetRef(document_id=HEX32, layer_id=HEX32, channel_id="../mask")

    def test_an_empty_channel_is_legal(self) -> None:
        """A raster layer is its own channel."""

        self.assertEqual("", TargetRef(document_id=HEX32,
                                       layer_id=HEX32).channel_id)

    def test_a_missing_document_or_layer_is_refused(self) -> None:
        with self.assertRaises(SchemaError):
            TargetRef(document_id="", layer_id=HEX32)
        with self.assertRaises(SchemaError):
            TargetRef(document_id=HEX32, layer_id="")

    def test_an_unknown_kind_is_refused_at_construction(self) -> None:
        """Not a branch nobody wrote falling through to `raster_layer` three
        layers down."""

        with self.assertRaises(ValueError):
            TargetRef(kind="everything", document_id=HEX32, layer_id=HEX32)

    def test_the_six_kinds_the_bundle_names_are_all_present(self) -> None:
        self.assertEqual(
            {"raster_layer", "layer_mask", "generation_mask", "selection",
             "region", "censorship"},
            {k.value for k in TargetKind})


class AssetFactsAreTheServersTests(unittest.TestCase):
    """Bundle §5: "The client cannot claim a hash/dimension and have it
    trusted." Studio has held this since WP1.2; these pin the shape."""

    def base(self, **over):
        args = dict(asset_id=ASSET_ID, sha256=HEX64, media_type="image/png",
                    width=512, height=384, byte_length=1024)
        args.update(over)
        return args

    def test_an_id_that_is_not_a_studio_handle_is_refused(self) -> None:
        for bad in ("", "asset-1", "studio-asset/nothex", "../etc",
                    f"studio-result/{HEX32}"):
            with self.subTest(value=bad), self.assertRaises(SchemaError):
                InputAssetRef(**self.base(asset_id=bad))

    def test_the_refusal_does_not_quote_the_value_back(self) -> None:
        """A malformed id and an unknown one are reported identically, so
        probing learns nothing -- the rule `asset_service._entry` holds."""

        with self.assertRaises(SchemaError) as caught:
            InputAssetRef(**self.base(asset_id="studio-asset/../../etc/passwd"))
        self.assertNotIn("passwd", str(caught.exception))

    def test_a_hash_that_is_not_a_bare_sha256_is_refused(self) -> None:
        """Bare hex. `sha256:`-prefixed is the mock's fixture-identity form and
        is not this -- AR5.4 found a Gallery key that could never link because
        the two were confused."""

        for bad in ("", HEX64.upper(), f"sha256:{HEX64}", HEX64[:-1]):
            with self.subTest(value=bad), self.assertRaises(SchemaError):
                InputAssetRef(**self.base(sha256=bad))

    def test_dimensions_are_required(self) -> None:
        """The whole reason this type exists: `AdmittedAsset` has none, and
        `input_assets.py` decodes them for the refusal and throws them away."""

        for name in ("width", "height", "byte_length"):
            for bad in (0, -1):
                with self.subTest(field=name, value=bad):
                    with self.assertRaises(SchemaError):
                        InputAssetRef(**self.base(**{name: bad}))


class TheProducerConsumerJoinIsEnumeratedTests(unittest.TestCase):
    """§11's actual requirement, and the only test here that can fail because
    somebody edited a DIFFERENT file."""

    def test_every_admitted_asset_field_has_a_disposition(self) -> None:
        declared = {f.name for f in fields(AdmittedAsset)}
        decided = set(ADMITTED_ASSET_DISPOSITION)
        missing = declared - decided
        self.assertEqual(
            set(), missing,
            f"AdmittedAsset gained {sorted(missing)} and nothing says what "
            "happens to it at the InputAssetRef join. Add it to "
            "ADMITTED_ASSET_DISPOSITION -- as a target field or as a named "
            "reason for dropping it.")

    def test_the_disposition_table_names_no_field_that_is_gone(self) -> None:
        declared = {f.name for f in fields(AdmittedAsset)}
        stale = set(ADMITTED_ASSET_DISPOSITION) - declared
        self.assertEqual(set(), stale,
                         f"the disposition table still decides {sorted(stale)}, "
                         "which AdmittedAsset no longer has")

    def test_every_mapped_target_is_a_real_field(self) -> None:
        targets = {f.name for f in fields(InputAssetRef)}
        for source, disposition in ADMITTED_ASSET_DISPOSITION.items():
            if ":" in disposition:
                continue                      # a named non-mapping
            with self.subTest(field=source):
                self.assertIn(disposition, targets)

    def test_every_ref_field_is_sourced_or_named_unsourced(self) -> None:
        """The other direction. A field on the consumer that nothing fills and
        nothing explains is a hole that reads as a zero."""

        mapped = {d for d in ADMITTED_ASSET_DISPOSITION.values() if ":" not in d}
        declared = {f.name for f in fields(InputAssetRef)}
        unexplained = declared - mapped - set(INPUT_ASSET_REF_UNSOURCED)
        self.assertEqual(
            set(), unexplained,
            f"InputAssetRef.{sorted(unexplained)} is filled by nothing and "
            "explained by nothing")

    def test_a_named_non_mapping_carries_its_reason(self) -> None:
        """"dropped" alone is not a decision. The point of the table is that
        somebody said WHY."""

        for source, disposition in ADMITTED_ASSET_DISPOSITION.items():
            if ":" not in disposition:
                continue
            with self.subTest(field=source):
                _, _, reason = disposition.partition(":")
                self.assertGreater(len(reason.strip()), 20)


class EveryRefusalIsSafeToShowTests(unittest.TestCase):
    """Bundle §10: stable code, user-safe message, retryability, support detail
    without paths or secrets."""

    REQUIRED = ("STALE_REVISION", "ASSET_MISSING", "TARGET_UNAVAILABLE",
                "RESOURCE_UNSUPPORTED", "RESOURCE_INTEGRITY",
                "COLOR_PROFILE_UNSUPPORTED", "STORAGE_UNAVAILABLE",
                "PROVIDER_UNAVAILABLE", "INSUFFICIENT_MEMORY")

    def test_every_case_the_bundle_names_has_an_entry(self) -> None:
        for name in self.REQUIRED:
            with self.subTest(case=name):
                self.assertIn(f"V2_{name}", REFUSALS)

    def test_no_message_contains_anything_path_shaped(self) -> None:
        for code, refusal in REFUSALS.items():
            with self.subTest(code=code):
                for token in ("/", "\\", "..", "C:", "file:"):
                    self.assertNotIn(token, refusal.message)

    def test_every_refusal_declares_its_retryability(self) -> None:
        for code, refusal in REFUSALS.items():
            with self.subTest(code=code):
                self.assertIsInstance(refusal.retryable, Retryable)

    def test_the_code_is_the_key(self) -> None:
        """A table whose keys can disagree with its values is a table that
        answers two different things depending on how it is read."""

        for code, refusal in REFUSALS.items():
            self.assertEqual(code, refusal.code)

    def test_insufficient_memory_does_not_imply_a_size_cap(self) -> None:
        """§10: "without imposing a silent global image-size cap". AR6.3
        removed Studio's invented pixel ceiling and the message must not
        reintroduce one as advice."""

        message = REFUSALS["V2_INSUFFICIENT_MEMORY"].message
        for banned in ("maximum", "limit", "too large", "not supported"):
            self.assertNotIn(banned, message.lower())


class ASnapshotIsFrozenAndCoherentTests(unittest.TestCase):
    def test_a_layer_cannot_be_both_included_and_excluded(self) -> None:
        """No defined contribution, and discovering it at composite time means
        discovering it as a wrong picture."""

        with self.assertRaises(SchemaError):
            GenerationSnapshot(document_id=HEX32, included_layer_ids=("a", "b"),
                               excluded_layer_ids=("b",))

    def test_an_operation_studio_does_not_perform_is_refused(self) -> None:
        with self.assertRaises(SchemaError):
            GenerationSnapshot(document_id=HEX32, operation="upscale")

    def test_a_path_shaped_document_is_refused(self) -> None:
        with self.assertRaises(SchemaError):
            GenerationSnapshot(document_id="../../doc")

    def test_the_layer_lists_are_tuples_so_a_snapshot_cannot_be_edited(self) -> None:
        """Frozen means frozen. A list would let a caller keep a reference and
        change what the job was admitted against."""

        snap = GenerationSnapshot(document_id=HEX32,
                                  included_layer_ids=["a", "b"])
        self.assertIsInstance(snap.included_layer_ids, tuple)


class TheSchemaModuleStaysPureTests(unittest.TestCase):
    """`forge_studio` is purity-locked, and a schema module is the easiest
    place to break that by reaching for a decoder."""

    def test_importing_it_drags_in_no_engine_and_no_imaging(self) -> None:
        """A CHILD INTERPRETER, the same instrument `test_import_boundaries`
        uses, because an in-process check reports whatever an earlier test
        happened to leave in `sys.modules`."""

        probe = ("import json, sys; import forge_studio.v2_contracts; "
                 "print(json.dumps(sorted(sys.modules)))")
        result = subprocess.run([sys.executable, "-c", probe],
                                cwd=str(APP_ROOT), capture_output=True,
                                text=True, check=False)
        self.assertEqual(0, result.returncode, result.stderr[-800:])
        loaded = set(json.loads(result.stdout))
        forbidden = {name for name in loaded
                     if name.split(".")[0] in
                     {"PIL", "torch", "gradio", "modules", "modules_forge",
                      "webui", "numpy"}}
        self.assertEqual(set(), forbidden)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_V2_CONTRACT_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
