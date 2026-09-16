"""Pure dependency-constraint decision engine."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Any, Iterable, Mapping, Sequence

from packaging.requirements import Requirement
from packaging.specifiers import InvalidSpecifier, Specifier
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version


DECISION_GO = "GO"
DECISION_NO_GO = "NO_GO"
DECISION_INCONCLUSIVE = "INCONCLUSIVE"

REASON_EMPTY_INTERSECTION = "DEPENDENCY_CONSTRAINT_INTERSECTION_EMPTY"
REASON_SATISFIABLE = "DEPENDENCY_CONSTRAINTS_SATISFIABLE"
REASON_INCONCLUSIVE = "DEPENDENCY_CONSTRAINT_EVALUATION_INCONCLUSIVE"
REASON_SELECTED_VERSION_OUTSIDE_CONSTRAINTS = (
    "DEPENDENCY_SELECTED_VERSION_OUTSIDE_CONSTRAINTS"
)
REASON_SELECTED_VERSION_MISSING = "DEPENDENCY_SELECTED_VERSION_MISSING"

MAX_EXHAUSTIVE_CORE_CONSTRAINTS = 12


class ConstraintEvaluationError(ValueError):
    """Raised when a constraint cannot be evaluated deterministically."""


@dataclass(frozen=True)
class DependencyConstraint:
    """One owner-attributed mandatory dependency requirement."""

    constraint_id: str
    dependency: str
    raw_requirement: str
    owner: Mapping[str, Any]
    source: Mapping[str, Any]
    direct: bool = False
    mandatory: bool = True
    marker_environment: Mapping[str, str] | None = None

    @property
    def normalized_dependency(self) -> str:
        return canonicalize_name(self.dependency)

    @property
    def parsed(self) -> Requirement:
        return Requirement(self.raw_requirement)

    @property
    def owner_display(self) -> str:
        return str(
            self.owner.get("display_name")
            or self.owner.get("name")
            or self.constraint_id
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DependencyConstraint":
        return cls(
            constraint_id=str(value["constraint_id"]),
            dependency=str(value["dependency"]),
            raw_requirement=str(value["raw_requirement"]),
            owner=dict(value["owner"]),
            source=dict(value["source"]),
            direct=bool(value.get("direct", False)),
            mandatory=bool(value.get("mandatory", True)),
            marker_environment=(
                {
                    str(key): str(child)
                    for key, child in dict(value["marker_environment"]).items()
                }
                if value.get("marker_environment") is not None
                else None
            ),
        )

    def to_fixture_dict(self) -> dict[str, Any]:
        """Return a lossless, round-trippable constraint record."""

        return {
            "constraint_id": self.constraint_id,
            "dependency": self.dependency,
            "normalized_dependency": self.normalized_dependency,
            "raw_requirement": self.raw_requirement,
            "owner": dict(self.owner),
            "source": dict(self.source),
            "direct": self.direct,
            "mandatory": self.mandatory,
            "marker_environment": (
                dict(self.marker_environment)
                if self.marker_environment is not None
                else None
            ),
        }

    def to_result_dict(self) -> dict[str, Any]:
        return {
            "constraint_id": self.constraint_id,
            "dependency": self.normalized_dependency,
            "owner": self.owner_display,
            "requirement": self.raw_requirement,
            "source": dict(self.source),
        }


@dataclass(frozen=True)
class ConstraintDecision:
    """Normalized dependency gate result."""

    decision: str
    reason_code: str
    dependency: str | None
    constraints: tuple[DependencyConstraint, ...]
    minimal_unsatisfiable_cores: tuple[tuple[str, ...], ...] = ()
    minimal_core_enumeration_complete: bool = True
    observed_version: Mapping[str, Any] | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "dependency-constraint-result/v1",
            "decision": self.decision,
            "reason_code": self.reason_code,
            "dependency": self.dependency,
            "constraints": [
                constraint.to_result_dict() for constraint in self.constraints
            ],
            "normalized_intersection": (
                "EMPTY"
                if self.reason_code == REASON_EMPTY_INTERSECTION
                else "NOT_PROVEN_EMPTY"
            ),
            "minimal_unsatisfiable_cores": [
                list(core) for core in self.minimal_unsatisfiable_cores
            ],
            "minimal_core_enumeration_complete": (
                self.minimal_core_enumeration_complete
            ),
            "observed_version": (
                dict(self.observed_version) if self.observed_version else None
            ),
            "selected_replacement": None,
            "recommended_replacement": None,
            "next_action": (
                "OWNER_DECISION_REQUIRED"
                if self.decision != DECISION_GO
                else "DEPENDENCY_GATE_PASSED"
            ),
            "detail": self.detail,
        }


@dataclass
class _Bounds:
    lower: Version | None = None
    lower_inclusive: bool = True
    upper: Version | None = None
    upper_inclusive: bool = True
    exact: set[Version] | None = None
    excluded: set[Version] | None = None

    def __post_init__(self) -> None:
        if self.exact is None:
            self.exact = set()
        if self.excluded is None:
            self.excluded = set()


class ConstraintDecisionEngine:
    """Validate submitted constraints without selecting replacement versions."""

    def evaluate(
        self,
        constraints: Sequence[DependencyConstraint],
        *,
        target_environment: Mapping[str, str] | None = None,
        observed_versions: Mapping[str, str] | None = None,
        require_observed_versions: bool = False,
    ) -> ConstraintDecision:
        ordered = tuple(sorted(constraints, key=self._sort_key))
        if not ordered:
            return ConstraintDecision(
                decision=DECISION_INCONCLUSIVE,
                reason_code=REASON_INCONCLUSIVE,
                dependency=None,
                constraints=(),
                detail="No dependency constraints were supplied.",
            )
        identifiers = [item.constraint_id for item in ordered]
        if len(identifiers) != len(set(identifiers)):
            return ConstraintDecision(
                decision=DECISION_INCONCLUSIVE,
                reason_code=REASON_INCONCLUSIVE,
                dependency=None,
                constraints=ordered,
                detail="Duplicate dependency constraint IDs were supplied.",
            )

        active: list[DependencyConstraint] = []
        errors: list[str] = []
        for constraint in ordered:
            try:
                if not constraint.mandatory:
                    continue
                parsed = constraint.parsed
                if canonicalize_name(parsed.name) != constraint.normalized_dependency:
                    raise ConstraintEvaluationError(
                        f"Requirement name mismatch in {constraint.constraint_id}"
                    )
                if parsed.marker is not None:
                    if target_environment is None:
                        raise ConstraintEvaluationError(
                            f"Marker environment missing for {constraint.constraint_id}"
                        )
                    marker_environment = dict(target_environment)
                    if constraint.marker_environment is not None:
                        marker_environment.update(constraint.marker_environment)
                    if not all(
                        isinstance(key, str) and isinstance(value, str)
                        for key, value in marker_environment.items()
                    ):
                        raise ConstraintEvaluationError(
                            f"Marker environment invalid for {constraint.constraint_id}"
                        )
                    if not parsed.marker.evaluate(environment=marker_environment):
                        continue
                active.append(constraint)
            except (
                ValueError,
                InvalidVersion,
                InvalidSpecifier,
                ConstraintEvaluationError,
            ) as exc:
                errors.append(f"{constraint.constraint_id}:{exc}")
        if errors:
            return ConstraintDecision(
                decision=DECISION_INCONCLUSIVE,
                reason_code=REASON_INCONCLUSIVE,
                dependency=None,
                constraints=ordered,
                detail="; ".join(sorted(errors)),
            )
        if not active:
            return ConstraintDecision(
                decision=DECISION_INCONCLUSIVE,
                reason_code=REASON_INCONCLUSIVE,
                dependency=None,
                constraints=ordered,
                detail="No active mandatory dependency constraints were supplied.",
            )

        grouped: dict[str, list[DependencyConstraint]] = {}
        for constraint in active:
            grouped.setdefault(constraint.normalized_dependency, []).append(constraint)

        normalized_observations: dict[str, str] = {}
        observation_errors: list[str] = []
        for name, raw_version in (observed_versions or {}).items():
            normalized = canonicalize_name(name)
            if normalized in normalized_observations:
                observation_errors.append(
                    f"Duplicate selected version for {normalized}"
                )
            normalized_observations[normalized] = str(raw_version)
        if observation_errors:
            return ConstraintDecision(
                decision=DECISION_INCONCLUSIVE,
                reason_code=REASON_INCONCLUSIVE,
                dependency=None,
                constraints=tuple(sorted(active, key=self._sort_key)),
                detail="; ".join(sorted(observation_errors)),
            )

        if require_observed_versions:
            for dependency in sorted(grouped):
                group = tuple(sorted(grouped[dependency], key=self._sort_key))
                if dependency not in normalized_observations:
                    return ConstraintDecision(
                        decision=DECISION_INCONCLUSIVE,
                        reason_code=REASON_SELECTED_VERSION_MISSING,
                        dependency=dependency,
                        constraints=group,
                        detail=(
                            "Complete candidate validation requires one selected "
                            f"version for {dependency}."
                        ),
                    )
                try:
                    observation = self._observation(
                        dependency, group, normalized_observations
                    )
                except InvalidVersion as exc:
                    return ConstraintDecision(
                        decision=DECISION_INCONCLUSIVE,
                        reason_code=REASON_INCONCLUSIVE,
                        dependency=dependency,
                        constraints=group,
                        detail=f"Invalid selected version for {dependency}: {exc}",
                    )
                assert observation is not None
                if observation["violates"]:
                    return ConstraintDecision(
                        decision=DECISION_NO_GO,
                        reason_code=REASON_SELECTED_VERSION_OUTSIDE_CONSTRAINTS,
                        dependency=dependency,
                        constraints=group,
                        observed_version=observation,
                        detail=(
                            "The selected candidate violates one or more active "
                            "mandatory constraints."
                        ),
                    )
            return ConstraintDecision(
                decision=DECISION_GO,
                reason_code=REASON_SATISFIABLE,
                dependency=None,
                constraints=tuple(sorted(active, key=self._sort_key)),
                detail=(
                    "Every active mandatory constraint has a selected candidate "
                    "that satisfies it. This does not authorize installation, "
                    "launch, or any replacement recommendation."
                ),
            )

        for dependency in sorted(grouped):
            group = tuple(sorted(grouped[dependency], key=self._sort_key))
            try:
                empty = self._intersection_empty(group)
            except (
                ConstraintEvaluationError,
                InvalidVersion,
                InvalidSpecifier,
            ) as exc:
                return ConstraintDecision(
                    decision=DECISION_INCONCLUSIVE,
                    reason_code=REASON_INCONCLUSIVE,
                    dependency=dependency,
                    constraints=group,
                    detail=str(exc),
                )
            if empty:
                cores, core_enumeration_complete = (
                    self._minimal_unsatisfiable_cores(group)
                )
                observation = self._observation(
                    dependency, group, observed_versions or {}
                )
                return ConstraintDecision(
                    decision=DECISION_NO_GO,
                    reason_code=REASON_EMPTY_INTERSECTION,
                    dependency=dependency,
                    constraints=group,
                    minimal_unsatisfiable_cores=cores,
                    minimal_core_enumeration_complete=(
                        core_enumeration_complete
                    ),
                    observed_version=observation,
                    detail="Mandatory dependency constraints have an empty intersection.",
                )

        all_constraints = tuple(sorted(active, key=self._sort_key))
        return ConstraintDecision(
            decision=DECISION_GO,
            reason_code=REASON_SATISFIABLE,
            dependency=None,
            constraints=all_constraints,
            detail=(
                "No empty mandatory constraint intersection was proven. "
                "This does not authorize launch or package mutation."
            ),
        )

    @staticmethod
    def _sort_key(constraint: DependencyConstraint) -> tuple[str, str, str]:
        return (
            constraint.constraint_id.casefold(),
            constraint.constraint_id,
            constraint.raw_requirement,
        )

    def _minimal_unsatisfiable_cores(
        self, constraints: Sequence[DependencyConstraint]
    ) -> tuple[tuple[tuple[str, ...], ...], bool]:
        cores: list[tuple[str, ...]] = []
        ordered = tuple(sorted(constraints, key=self._sort_key))
        if len(ordered) > MAX_EXHAUSTIVE_CORE_CONSTRAINTS:
            reduced = list(ordered)
            for constraint in ordered:
                candidate = [
                    item
                    for item in reduced
                    if item.constraint_id != constraint.constraint_id
                ]
                if candidate and self._intersection_empty(candidate):
                    reduced = candidate
            core = tuple(sorted(item.constraint_id for item in reduced))
            return ((core,) if core else ()), False

        for size in range(1, len(ordered) + 1):
            for subset in combinations(ordered, size):
                subset_ids = frozenset(item.constraint_id for item in subset)
                if any(set(core).issubset(subset_ids) for core in cores):
                    continue
                try:
                    if self._intersection_empty(subset):
                        cores.append(tuple(sorted(subset_ids)))
                except (
                    ConstraintEvaluationError,
                    InvalidVersion,
                    InvalidSpecifier,
                ):
                    continue
        return tuple(sorted(set(cores))), True

    def _intersection_empty(
        self, constraints: Sequence[DependencyConstraint]
    ) -> bool:
        bounds = _Bounds()
        specifiers: list[Specifier] = []
        for constraint in constraints:
            parsed = constraint.parsed
            specifiers.extend(sorted(parsed.specifier, key=str))
        for specifier in specifiers:
            self._apply_specifier(bounds, specifier)

        assert bounds.exact is not None
        assert bounds.excluded is not None
        if bounds.exact:
            return not any(
                all(
                    candidate in constraint.parsed.specifier
                    for constraint in constraints
                )
                for candidate in sorted(bounds.exact)
            )

        if bounds.lower is not None and bounds.upper is not None:
            if bounds.lower > bounds.upper:
                return True
            if bounds.lower == bounds.upper:
                return not (
                    bounds.lower_inclusive
                    and bounds.upper_inclusive
                    and bounds.lower not in bounds.excluded
                )
        return False

    def _apply_specifier(self, bounds: _Bounds, specifier: Specifier) -> None:
        operator = specifier.operator
        raw_version = specifier.version

        if operator == "===":
            raise ConstraintEvaluationError(
                f"Arbitrary equality is not supported: {specifier}"
            )

        if operator == "==":
            if raw_version.endswith(".*"):
                lower = Version(raw_version[:-2])
                upper = self._wildcard_upper(lower)
                self._apply_lower(bounds, lower, inclusive=True)
                self._apply_upper(bounds, upper, inclusive=False)
                return
            bounds.exact.add(Version(raw_version))
            return

        if operator == "!=":
            if raw_version.endswith(".*"):
                raise ConstraintEvaluationError(
                    f"Wildcard exclusion is not supported: {specifier}"
                )
            bounds.excluded.add(Version(raw_version))
            return

        if operator == "~=":
            lower = Version(raw_version)
            self._require_simple_symbolic_version(specifier, lower)
            upper = self._compatible_upper(lower)
            self._apply_lower(bounds, lower, inclusive=True)
            self._apply_upper(bounds, upper, inclusive=False)
            return

        version = Version(raw_version)
        self._require_simple_symbolic_version(specifier, version)
        if operator == ">=":
            self._apply_lower(bounds, version, inclusive=True)
        elif operator == ">":
            self._apply_lower(bounds, version, inclusive=False)
        elif operator == "<=":
            self._apply_upper(bounds, version, inclusive=True)
        elif operator == "<":
            self._apply_upper(bounds, version, inclusive=False)
        else:
            raise ConstraintEvaluationError(f"Unsupported specifier: {specifier}")

    @staticmethod
    def _require_simple_symbolic_version(
        specifier: Specifier, version: Version
    ) -> None:
        """Fail closed where ordered PEP 440 semantics exceed simple bounds."""

        if (
            version.is_prerelease
            or version.is_postrelease
            or version.is_devrelease
            or version.local is not None
        ):
            raise ConstraintEvaluationError(
                "Symbolic ordered comparison is unsupported for pre/dev/post/"
                f"local versions: {specifier}"
            )

    @staticmethod
    def _apply_lower(bounds: _Bounds, version: Version, inclusive: bool) -> None:
        if bounds.lower is None or version > bounds.lower:
            bounds.lower = version
            bounds.lower_inclusive = inclusive
        elif version == bounds.lower:
            bounds.lower_inclusive = bounds.lower_inclusive and inclusive

    @staticmethod
    def _apply_upper(bounds: _Bounds, version: Version, inclusive: bool) -> None:
        if bounds.upper is None or version < bounds.upper:
            bounds.upper = version
            bounds.upper_inclusive = inclusive
        elif version == bounds.upper:
            bounds.upper_inclusive = bounds.upper_inclusive and inclusive

    @staticmethod
    def _wildcard_upper(version: Version) -> Version:
        release = list(version.release)
        if not release:
            raise ConstraintEvaluationError("Wildcard version has no release segment")
        release[-1] += 1
        prefix = f"{version.epoch}!" if version.epoch else ""
        return Version(prefix + ".".join(str(part) for part in release))

    @staticmethod
    def _compatible_upper(version: Version) -> Version:
        release = list(version.release)
        if len(release) < 2:
            raise ConstraintEvaluationError(
                f"Compatible release requires two segments: {version}"
            )
        prefix = release[:-1]
        prefix[-1] += 1
        epoch = f"{version.epoch}!" if version.epoch else ""
        return Version(epoch + ".".join(str(part) for part in prefix))

    @staticmethod
    def _observation(
        dependency: str,
        constraints: Sequence[DependencyConstraint],
        observed_versions: Mapping[str, str],
    ) -> Mapping[str, Any] | None:
        raw_version = next(
            (
                version
                for name, version in observed_versions.items()
                if canonicalize_name(name) == dependency
            ),
            None,
        )
        if raw_version is None:
            return None
        version = Version(raw_version)
        satisfies: list[str] = []
        violates: list[str] = []
        for constraint in constraints:
            destination = (
                satisfies
                if version in constraint.parsed.specifier
                else violates
            )
            destination.append(constraint.constraint_id)
        return {
            "version": str(version),
            "satisfies": sorted(satisfies),
            "violates": sorted(violates),
        }


def constraints_from_fixture(
    fixture: Mapping[str, Any],
) -> tuple[DependencyConstraint, ...]:
    if "constraints" not in fixture or not isinstance(
        fixture["constraints"], list
    ):
        raise ValueError("FIXTURE_CONSTRAINTS_REQUIRED")
    return tuple(
        DependencyConstraint.from_dict(item)
        for item in fixture["constraints"]
    )


def constraints_from_requirements(
    requirements: Iterable[tuple[str, str, Mapping[str, Any], Mapping[str, Any]]]
) -> tuple[DependencyConstraint, ...]:
    """Build normalized constraints from simple adapter tuples."""

    return tuple(
        DependencyConstraint(
            constraint_id=constraint_id,
            dependency=Requirement(raw_requirement).name,
            raw_requirement=raw_requirement,
            owner=dict(owner),
            source=dict(source),
        )
        for constraint_id, raw_requirement, owner, source in requirements
    )
