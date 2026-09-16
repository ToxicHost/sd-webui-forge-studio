from __future__ import annotations

from itertools import permutations
import json
import os
from pathlib import Path
import unittest

from scripts.preflight.boundary import WorkspaceBoundary, WorkspaceFS
from scripts.preflight.constraints import (
    ConstraintDecisionEngine,
    DependencyConstraint,
    DECISION_GO,
    DECISION_INCONCLUSIVE,
    DECISION_NO_GO,
    REASON_EMPTY_INTERSECTION,
    constraints_from_fixture,
)


WORKSPACE = Path(os.path.abspath(__file__)).parents[4]
FIXTURE = (
    WORKSPACE
    / "app"
    / "scripts"
    / "preflight"
    / "tests"
    / "fixtures"
    / "dependency-conflict.json"
)


class ConstraintDecisionEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        fs = WorkspaceFS(WorkspaceBoundary(WORKSPACE))
        cls.fixture = json.loads(fs.read_text(FIXTURE))
        cls.constraints = constraints_from_fixture(cls.fixture)
        cls.engine = ConstraintDecisionEngine()

    def evaluate(self, constraints=None):
        return self.engine.evaluate(
            constraints or self.constraints,
            target_environment=self.fixture["target_environment"],
            observed_versions=self.fixture["observed_versions"],
        )

    def test_conflict_fixture_returns_required_no_go(self) -> None:
        decision = self.evaluate()
        result = decision.to_dict()
        self.assertEqual(DECISION_NO_GO, decision.decision)
        self.assertEqual(REASON_EMPTY_INTERSECTION, decision.reason_code)
        self.assertEqual("pillow", decision.dependency)
        self.assertEqual("EMPTY", result["normalized_intersection"])
        self.assertIsNone(result["selected_replacement"])
        self.assertIsNone(result["recommended_replacement"])
        self.assertEqual(
            set(self.fixture["expected"]["owner_ids"]),
            {item["constraint_id"] for item in result["constraints"]},
        )

    def test_result_is_deterministic_for_all_constraint_orders(self) -> None:
        baseline = self.evaluate().to_dict()
        for ordered in permutations(self.constraints):
            with self.subTest(order=[item.constraint_id for item in ordered]):
                self.assertEqual(baseline, self.evaluate(ordered).to_dict())

    def test_both_minimal_unsatisfiable_cores_are_reported(self) -> None:
        cores = {
            tuple(core)
            for core in self.evaluate().to_dict()["minimal_unsatisfiable_cores"]
        }
        self.assertIn(
            tuple(
                sorted(
                    (
                        "repository:Pillow:requirements.txt:2",
                        "distribution:gradio:4.40.0:pillow",
                    )
                )
            ),
            cores,
        )
        self.assertIn(
            tuple(
                sorted(
                    (
                        "distribution:gradio:4.40.0:pillow",
                        "distribution:pillow-heif:1.4.0:pillow",
                    )
                )
            ),
            cores,
        )

    def test_gradio_and_pillow_heif_conflict_without_repository_pin(self) -> None:
        without_repository = tuple(
            item for item in self.constraints if not item.direct
        )
        self.assertEqual(DECISION_NO_GO, self.evaluate(without_repository).decision)

    def test_removing_gradio_makes_current_pillow_constraints_satisfiable(self) -> None:
        without_gradio = tuple(
            item for item in self.constraints if "gradio" not in item.constraint_id
        )
        self.assertEqual(DECISION_GO, self.evaluate(without_gradio).decision)

    def test_observed_pillow_violates_gradio_only(self) -> None:
        observation = self.evaluate().to_dict()["observed_version"]
        self.assertEqual("12.3.0", observation["version"])
        self.assertEqual(
            ["distribution:gradio:4.40.0:pillow"],
            observation["violates"],
        )

    def test_empty_evidence_is_inconclusive(self) -> None:
        self.assertEqual(
            DECISION_INCONCLUSIVE,
            self.engine.evaluate(()).decision,
        )

    def test_three_way_minimal_core_is_reported(self) -> None:
        constraints = tuple(
            DependencyConstraint(
                constraint_id=identifier,
                dependency="example",
                raw_requirement=requirement,
                owner={"display_name": identifier},
                source={"source_id": "test"},
            )
            for identifier, requirement in (
                ("lower", "example>=1"),
                ("upper", "example<=1"),
                ("exclude", "example!=1"),
            )
        )
        result = self.engine.evaluate(constraints).to_dict()
        self.assertEqual(DECISION_NO_GO, result["decision"])
        self.assertEqual(
            [["exclude", "lower", "upper"]],
            result["minimal_unsatisfiable_cores"],
        )

    def test_pep440_local_equality_overlap_is_not_false_no_go(self) -> None:
        constraints = tuple(
            DependencyConstraint(
                constraint_id=identifier,
                dependency="example",
                raw_requirement=requirement,
                owner={"display_name": identifier},
                source={"source_id": "test"},
            )
            for identifier, requirement in (
                ("public", "example==1.0"),
                ("local", "example==1.0+vendor"),
            )
        )
        self.assertEqual(DECISION_GO, self.engine.evaluate(constraints).decision)

    def test_epoch_is_preserved_for_wildcard_bounds(self) -> None:
        constraint = DependencyConstraint(
            constraint_id="epoch",
            dependency="example",
            raw_requirement="example==1!1.4.*",
            owner={"display_name": "epoch"},
            source={"source_id": "test"},
        )
        self.assertEqual(
            DECISION_GO, self.engine.evaluate((constraint,)).decision
        )

    def test_arbitrary_equality_fails_inconclusive(self) -> None:
        constraint = DependencyConstraint(
            constraint_id="arbitrary",
            dependency="example",
            raw_requirement="example===vendor-build",
            owner={"display_name": "arbitrary"},
            source={"source_id": "test"},
        )
        self.assertEqual(
            DECISION_INCONCLUSIVE,
            self.engine.evaluate((constraint,)).decision,
        )

    def test_selected_candidate_must_satisfy_every_active_constraint(self) -> None:
        constraints = (
            DependencyConstraint(
                constraint_id="owner:lower",
                dependency="example",
                raw_requirement="example>=8",
                owner={"display_name": "lower"},
                source={"fixture": "selected"},
            ),
            DependencyConstraint(
                constraint_id="owner:upper",
                dependency="example",
                raw_requirement="example<11",
                owner={"display_name": "upper"},
                source={"fixture": "selected"},
            ),
        )
        result = self.engine.evaluate(
            constraints,
            observed_versions={"example": "12.3"},
            require_observed_versions=True,
        )
        self.assertEqual(DECISION_NO_GO, result.decision)
        self.assertEqual(
            "DEPENDENCY_SELECTED_VERSION_OUTSIDE_CONSTRAINTS",
            result.reason_code,
        )
        self.assertEqual(
            ["owner:upper"],
            result.observed_version["violates"],
        )
        self.assertEqual(
            "NOT_PROVEN_EMPTY",
            result.to_dict()["normalized_intersection"],
        )

    def test_complete_candidate_validation_requires_observation(self) -> None:
        constraint = DependencyConstraint(
            constraint_id="owner:required",
            dependency="example",
            raw_requirement="example>=1",
            owner={"display_name": "required"},
            source={"fixture": "selected"},
        )
        result = self.engine.evaluate(
            (constraint,),
            observed_versions={},
            require_observed_versions=True,
        )
        self.assertEqual(DECISION_INCONCLUSIVE, result.decision)
        self.assertEqual(
            "DEPENDENCY_SELECTED_VERSION_MISSING",
            result.reason_code,
        )

    def test_symbolic_post_release_bounds_fail_inconclusive(self) -> None:
        constraints = (
            DependencyConstraint(
                constraint_id="owner:lower",
                dependency="example",
                raw_requirement="example>1.0",
                owner={"display_name": "lower"},
                source={"fixture": "post"},
            ),
            DependencyConstraint(
                constraint_id="owner:upper",
                dependency="example",
                raw_requirement="example<=1.0.post1",
                owner={"display_name": "upper"},
                source={"fixture": "post"},
            ),
        )
        result = self.engine.evaluate(constraints)
        self.assertEqual(DECISION_INCONCLUSIVE, result.decision)

    def test_constraint_fixture_serialization_round_trips(self) -> None:
        original = DependencyConstraint(
            constraint_id="owner:roundtrip",
            dependency="Example",
            raw_requirement="Example>=1; python_version >= '3.13'",
            owner={"kind": "fixture", "display_name": "roundtrip"},
            source={"path": "fixture.json", "line": 1},
            direct=True,
            mandatory=True,
            marker_environment={"extra": ""},
        )
        restored = DependencyConstraint.from_dict(original.to_fixture_dict())
        self.assertEqual(original, restored)

    def test_large_unsatisfiable_group_uses_bounded_core_reduction(self) -> None:
        constraints = [
            DependencyConstraint(
                constraint_id="owner:lower",
                dependency="example",
                raw_requirement="example>=2",
                owner={"display_name": "lower"},
                source={"fixture": "large"},
            ),
            DependencyConstraint(
                constraint_id="owner:upper",
                dependency="example",
                raw_requirement="example<2",
                owner={"display_name": "upper"},
                source={"fixture": "large"},
            ),
        ]
        for index in range(11):
            constraints.append(
                DependencyConstraint(
                    constraint_id=f"owner:extra:{index:02d}",
                    dependency="example",
                    raw_requirement="example>=0",
                    owner={"display_name": f"extra {index}"},
                    source={"fixture": "large"},
                )
            )
        result = self.engine.evaluate(tuple(constraints))
        self.assertEqual(DECISION_NO_GO, result.decision)
        self.assertFalse(result.minimal_core_enumeration_complete)
        self.assertEqual(1, len(result.minimal_unsatisfiable_cores))


if __name__ == "__main__":
    unittest.main()
