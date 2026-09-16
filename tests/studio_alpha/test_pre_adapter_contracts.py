"""Contract readiness tests that do not import or launch Forge."""

from __future__ import annotations

import unittest

from forge_studio.contracts import GeneratedResult, JobState, ModelSummary
from forge_studio.mock_backend import MockBackend
from forge_studio.presentation import StudioPresentation


class PreAdapterContractTests(unittest.TestCase):
    def test_generated_result_supports_optional_data_url_transport(self) -> None:
        data_url = "data:image/png;base64,c3R1ZGlv"
        legacy = GeneratedResult(
            "job-data-url",
            JobState.COMPLETED,
            "image/png",
            data_url,
            {"transport": "data-url"},
        )
        self.assertEqual(data_url, legacy.image_data_url)
        self.assertEqual(data_url, legacy.to_dict()["image_data_url"])

        referenced = GeneratedResult(
            job_id="job-owned-reference",
            state=JobState.COMPLETED,
            mime_type="image/png",
            metadata={"transport": "owned-reference"},
            output_path="owned-results/job-owned-reference.png",
        )
        self.assertIsNone(referenced.image_data_url)
        self.assertNotIn("image_data_url", referenced.to_dict())
        self.assertEqual(
            "owned-results/job-owned-reference.png",
            referenced.to_dict()["output_path"],
        )

        with self.assertRaises(ValueError):
            GeneratedResult(
                job_id="job-without-transport",
                state=JobState.COMPLETED,
                mime_type="image/png",
                metadata={},
            )

    def test_model_summary_defaults_to_real_classification(self) -> None:
        model = ModelSummary(
            model_id="future-real-model",
            name="Future Real Model",
            description="Contract-only real backend fixture.",
        )
        self.assertFalse(model.is_mock)
        self.assertFalse(model.to_dict()["is_mock"])

    def test_mock_backend_models_are_explicitly_mock(self) -> None:
        self.assertTrue(MockBackend.MODELS)
        self.assertTrue(all(model.is_mock for model in MockBackend.MODELS))

    def test_presentation_never_exposes_backend_paths(self) -> None:
        class CompletedApplication:
            @staticmethod
            def poll_or_stream_progress(_job_id: str) -> dict[str, object]:
                return {"state": "completed", "progress": 100}

            @staticmethod
            def get_result(_job_id: str) -> GeneratedResult:
                return GeneratedResult(
                    job_id="job-private-path",
                    state=JobState.COMPLETED,
                    mime_type="image/png",
                    metadata={"transport": "owned-reference"},
                    output_path=r"C:\private\result.png",
                    metadata_path=r"C:\private\result.json",
                )

        presentation = StudioPresentation(CompletedApplication(), object)
        result = presentation.poll("job-private-path")["result"]
        self.assertNotIn("output_path", result)
        self.assertNotIn("metadata_path", result)


if __name__ == "__main__":
    unittest.main()
