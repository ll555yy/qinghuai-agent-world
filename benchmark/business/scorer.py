"""Ground-truth task scoring and paired business-result aggregation."""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


def _read(value: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="python")
        return dumped if isinstance(dumped, Mapping) else {}
    if hasattr(value, "__dict__"):
        return vars(value)
    return {}


def _lookup(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    """Look up the first present key, accepting camelCase/snake_case aliases."""

    for key in keys:
        if key in mapping:
            return mapping[key]
        # Common records use snake_case while artifacts use camelCase.
        snake = []
        for char in key:
            snake.append("_" + char.lower() if char.isupper() else char)
        snake_key = "".join(snake).lstrip("_")
        if snake_key in mapping:
            return mapping[snake_key]
    return default


def _relationship_value(relationships: Any, from_actor: str, to_actor: str) -> Mapping[str, Any]:
    if not isinstance(relationships, Mapping):
        return {}
    keys = (
        f"{from_actor}|{to_actor}",
        f"{from_actor}:{to_actor}",
        f"{from_actor}->{to_actor}",
        (from_actor, to_actor),
    )
    for key in keys:
        if key in relationships and isinstance(relationships[key], Mapping):
            return relationships[key]
    # Production projections sometimes use a nested source -> target shape.
    source = relationships.get(from_actor)
    if isinstance(source, Mapping):
        target = source.get(to_actor)
        if isinstance(target, Mapping):
            return target
    return {}


def _equal(actual: Any, expected: Any) -> bool:
    # YAML numbers and persisted numeric fields can differ in concrete type,
    # but booleans must not equal 1/0 for a state contract.
    if isinstance(actual, bool) or isinstance(expected, bool):
        return actual is expected
    return actual == expected


@dataclass(frozen=True, slots=True)
class SuccessScore:
    success: bool
    task_id: str | None = None
    checks_total: int = 0
    checks_passed: int = 0
    goal_completion_ratio: float | None = None
    completion_step: int | None = None
    failure_type: str | None = None
    failure_reasons: tuple[str, ...] = ()
    hard_failures: tuple[str, ...] = ()
    invalid_action_count: int = 0

    @property
    def goal_completion_rate(self) -> float | None:
        return self.goal_completion_ratio

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "taskId": self.task_id,
            "checksTotal": self.checks_total,
            "checksPassed": self.checks_passed,
            "goalCompletionRatio": self.goal_completion_ratio,
            "completionStep": self.completion_step,
            "failureType": self.failure_type,
            "failureReasons": list(self.failure_reasons),
            "hardFailures": list(self.hard_failures),
            "invalidActionCount": self.invalid_action_count,
        }


def _check_mapping(
    state: Mapping[str, Any],
    container_key: str,
    expected: Mapping[str, Any],
    reasons: list[str],
) -> tuple[int, int]:
    container = _lookup(state, container_key, {})
    if not isinstance(container, Mapping):
        container = {}
    total = passed = 0
    for key, wanted in expected.items():
        total += 1
        actual = _lookup(container, str(key))
        if _equal(actual, wanted):
            passed += 1
        else:
            reasons.append(f"{container_key}.{key}: expected {wanted!r}, got {actual!r}")
    return total, passed


def _check_goal_requirements(
    state: Mapping[str, Any],
    expected: Any,
    reasons: list[str],
) -> tuple[int, int]:
    goals = _lookup(state, "goals", {})
    if not isinstance(goals, Mapping):
        goals = {}
    if isinstance(expected, Mapping):
        pairs = expected.items()
    elif isinstance(expected, Sequence) and not isinstance(expected, (str, bytes)):
        pairs = ((str(goal_id), "achieved") for goal_id in expected)
    else:
        pairs = ()
    total = passed = 0
    for goal_id, wanted in pairs:
        total += 1
        goal = goals.get(goal_id, {})
        actual = _lookup(goal, "status") if isinstance(goal, Mapping) else goal
        if _equal(actual, wanted) or (wanted in {"completed", "done"} and actual == "achieved"):
            passed += 1
        else:
            reasons.append(f"goals.{goal_id}.status: expected {wanted!r}, got {actual!r}")
    return total, passed


def _check_relationships(
    state: Mapping[str, Any],
    requirements: Any,
    reasons: list[str],
) -> tuple[int, int]:
    if not isinstance(requirements, Sequence) or isinstance(requirements, (str, bytes)):
        return 0, 0
    relationships = _lookup(state, "relationships", {})
    total = passed = 0
    for requirement in requirements:
        if not isinstance(requirement, Mapping):
            continue
        from_actor = str(_lookup(requirement, "fromActorId", "from_actor_id", default=""))
        to_actor = str(_lookup(requirement, "toActorId", "to_actor_id", default=""))
        relation_field = str(_lookup(requirement, "field", default=""))
        relation = _relationship_value(relationships, from_actor, to_actor)
        actual = _lookup(relation, relation_field)
        total += 1
        minimum = _lookup(requirement, "min", default=None)
        maximum = _lookup(requirement, "max", default=None)
        expected = _lookup(requirement, "equals", "value", default=None)
        ok = True
        if minimum is not None:
            try:
                ok = ok and actual is not None and float(actual) >= float(minimum)
            except (TypeError, ValueError):
                ok = False
        if maximum is not None:
            try:
                ok = ok and actual is not None and float(actual) <= float(maximum)
            except (TypeError, ValueError):
                ok = False
        if expected is not None:
            ok = ok and _equal(actual, expected)
        if ok:
            passed += 1
        else:
            reasons.append(
                f"relationships.{from_actor}|{to_actor}.{relation_field}: "
                f"constraints min={minimum!r}, max={maximum!r}, equals={expected!r}, got {actual!r}"
            )
    return total, passed


def _check_state(state: Mapping[str, Any], success_contract: Mapping[str, Any]) -> tuple[int, int, list[str]]:
    reasons: list[str] = []
    total = passed = 0

    min_day = _lookup(success_contract, "minWorldDay", "min_world_day", default=None)
    if min_day is not None:
        total += 1
        actual_day = _lookup(state, "worldDay", "world_day", default=None)
        try:
            ok = actual_day is not None and int(actual_day) >= int(min_day)
        except (TypeError, ValueError):
            ok = False
        if ok:
            passed += 1
        else:
            reasons.append(f"worldDay: expected >= {min_day!r}, got {actual_day!r}")

    for contract_key, state_key in (
        ("requiredAuthorization", "authorization"),
        ("requiredCommitments", "commitments"),
        ("requiredChapter", "chapter"),
    ):
        expected = _lookup(success_contract, contract_key)
        if isinstance(expected, Mapping):
            checks, successes = _check_mapping(state, state_key, expected, reasons)
            total += checks
            passed += successes

    expected_goals = _lookup(success_contract, "requiredGoals", "required_goals", default=None)
    if expected_goals is not None:
        checks, successes = _check_goal_requirements(state, expected_goals, reasons)
        total += checks
        passed += successes

    relationship_requirements = _lookup(success_contract, "relationshipMinimums", "relationship_minimums", default=None)
    if relationship_requirements is None:
        relationship_requirements = _lookup(success_contract, "relationships", default=None)
    if relationship_requirements is not None:
        checks, successes = _check_relationships(state, relationship_requirements, reasons)
        total += checks
        passed += successes

    # A task can explicitly require action ids when an adapter writes a state
    # that does not expose all derived chapter fields.
    required_actions = _lookup(success_contract, "requiredActions", "required_actions", default=None)
    if required_actions is not None:
        trace_ids = {
            str(_lookup(item, "actionId", "action_id", default=""))
            for item in _lookup(state, "trace", default=()) or ()
            if isinstance(item, Mapping)
        }
        for action in required_actions if isinstance(required_actions, Sequence) else ():
            total += 1
            if str(action) in trace_ids:
                passed += 1
            else:
                reasons.append(f"trace missing required action {action!r}")
    return total, passed, reasons


def score_task_state(
    task: Mapping[str, Any],
    state: Mapping[str, Any],
    trace: Sequence[Mapping[str, Any]] = (),
) -> SuccessScore:
    """Score a task using only its authoritative state contract."""

    task_id = str(_lookup(task, "taskId", "task_id", default="")) or None
    contract = _lookup(task, "success", default={})
    if not isinstance(contract, Mapping):
        contract = {}
    state_with_trace = dict(state)
    state_with_trace.setdefault("trace", list(trace))
    total, passed, reasons = _check_state(state_with_trace, contract)
    invalid_action_count = sum(
        1
        for item in trace
        if isinstance(item, Mapping) and _lookup(item, "valid", default=True) is False
    )
    if invalid_action_count:
        failure_type = "invalid_action"
    elif reasons:
        first = reasons[0]
        if first.startswith("worldDay"):
            failure_type = "temporal_constraint"
        elif first.startswith("relationships"):
            failure_type = "relationship_constraint"
        elif first.startswith("authorization"):
            failure_type = "authorization_missing"
        elif first.startswith("commitments"):
            failure_type = "commitment_missing"
        elif first.startswith("chapter"):
            failure_type = "chapter_incomplete"
        else:
            failure_type = "goal_incomplete"
    else:
        failure_type = None
    ratio = passed / total if total else (1.0 if not reasons else 0.0)
    completion_step = None
    if not reasons:
        for item in reversed(trace):
            step = _lookup(item, "step", default=None)
            if step is not None:
                completion_step = int(step)
                break
    return SuccessScore(
        success=not reasons,
        task_id=task_id,
        checks_total=total,
        checks_passed=passed,
        goal_completion_ratio=ratio,
        completion_step=completion_step,
        failure_type=failure_type,
        failure_reasons=tuple(reasons),
        hard_failures=tuple(reasons),
        invalid_action_count=invalid_action_count,
    )


def score_attempt(attempt: Any, task: Mapping[str, Any] | None = None) -> SuccessScore:
    """Score a runner result, mapping, or common ``AttemptRecord`` instance."""

    result_task = _read(attempt, "task", default=None)
    if task is None and result_task is not None:
        task = _read(result_task, "raw", default=result_task)
    if task is None:
        task = _read(attempt, "taskSpec", "task_spec", default={})
    task_map = _mapping(task)
    state = _read(attempt, "state", default={})
    trace = _read(attempt, "trace", default=())
    if not isinstance(state, Mapping):
        state = {}
    if not isinstance(trace, Sequence) or isinstance(trace, (str, bytes)):
        trace = ()
    return score_task_state(task_map, state, trace)


def _result_field(result: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        value = _read(result, name, default=None)
        if value is not None:
            return value
    attempt = _read(result, "attempt", default=None)
    if attempt is not None:
        return _read(attempt, *names, default=default)
    return default


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def paired_bootstrap_ci(
    differences: Sequence[float],
    *,
    resamples: int = 10_000,
    seed: int = 20260901,
) -> tuple[float | None, float | None]:
    """Return a deterministic percentile CI for paired differences."""

    if not differences:
        return None, None
    if resamples <= 0:
        raise ValueError("resamples must be positive")
    values = tuple(float(value) for value in differences)
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(resamples):
        means.append(sum(rng.choice(values) for _ in values) / len(values))
    return _percentile(means, 0.025), _percentile(means, 0.975)


def _telemetry_metrics(result: Any) -> dict[str, Any]:
    telemetry = _result_field(result, "telemetry", default={})
    if not isinstance(telemetry, Mapping):
        telemetry = _mapping(telemetry)
    return {
        "calls": _lookup(telemetry, "calls", default=0) or 0,
        "totalTokens": _lookup(telemetry, "totalTokens", "total_tokens", default=None),
        "durationMs": _lookup(telemetry, "durationMs", "duration_ms", default=None),
        "costCnyEstimated": _lookup(telemetry, "costCnyEstimated", "cost_cny_estimated", default=None),
    }


def _condition_summary(results: Sequence[Any]) -> dict[str, Any]:
    valid = [result for result in results if bool(_result_field(result, "infraValid", "infra_valid", default=True))]
    successes = sum(bool(_result_field(result, "gameplaySuccess", "gameplay_success", default=False)) for result in valid)
    statuses = Counter(str(_result_field(result, "failureType", "failure_type", default="unknown")) for result in results if not bool(_result_field(result, "gameplaySuccess", "gameplay_success", default=False)))
    steps = [_result_field(result, "steps", "stepCount", "step_count", default=0) for result in valid]
    calls = [_telemetry_metrics(result)["calls"] for result in valid]
    durations = [item for item in (_telemetry_metrics(result)["durationMs"] for result in valid) if item is not None]
    costs = [item for item in (_telemetry_metrics(result)["costCnyEstimated"] for result in valid) if item is not None]
    return {
        "attempts": len(results),
        "infraInvalid": len(results) - len(valid),
        "validAttempts": len(valid),
        "successes": successes,
        "taskSuccessRate": successes / len(valid) if valid else None,
        "meanCompletionSteps": sum(steps) / len(steps) if steps else None,
        "meanCalls": sum(calls) / len(calls) if calls else None,
        "p95DurationMs": _percentile([float(value) for value in durations], 0.95),
        "meanCostCnyEstimated": sum(float(value) for value in costs) / len(costs) if costs else None,
        "failureTaxonomy": dict(sorted(statuses.items())),
    }


def aggregate_paired_results(
    results: Iterable[Any],
    *,
    bootstrap_resamples: int = 10_000,
    bootstrap_seed: int = 20260901,
) -> dict[str, Any]:
    """Aggregate conditions while preserving every raw attempt and pair."""

    materialized = list(results)
    by_condition: dict[str, list[Any]] = defaultdict(list)
    pairs: dict[tuple[str, str, int, str], dict[str, Any]] = defaultdict(dict)
    for result in materialized:
        condition = str(_result_field(result, "conditionId", "condition_id", "policyId", "policy_id", default="unknown"))
        by_condition[condition].append(result)
        task_id = str(_result_field(result, "taskId", "task_id", default=""))
        route = str(_result_field(result, "route", default="autonomous"))
        seed = int(_result_field(result, "seed", default=0))
        pair_id = str(_result_field(result, "pairedGroupId", "paired_group_id", default=f"{task_id}:{route}:{seed}"))
        pairs[(task_id, route, seed, pair_id)][condition] = result

    condition_summaries = {condition: _condition_summary(items) for condition, items in sorted(by_condition.items())}
    paired_effects: dict[str, Any] = {}
    for baseline_id, label in (("B1_random_legal", "fullVsRandom"), ("B2_myopic_rule", "fullVsMyopic")):
        differences: list[float] = []
        paired_count = 0
        excluded_pairs = 0
        for pair in pairs.values():
            full = pair.get("A0_full")
            baseline = pair.get(baseline_id)
            if full is None or baseline is None:
                continue
            if not bool(_result_field(full, "infraValid", "infra_valid", default=True)) or not bool(_result_field(baseline, "infraValid", "infra_valid", default=True)):
                excluded_pairs += 1
                continue
            full_success = bool(_result_field(full, "gameplaySuccess", "gameplay_success", default=False))
            baseline_success = bool(_result_field(baseline, "gameplaySuccess", "gameplay_success", default=False))
            differences.append(float(full_success) - float(baseline_success))
            paired_count += 1
        ci_low, ci_high = paired_bootstrap_ci(
            differences,
            resamples=bootstrap_resamples,
            seed=bootstrap_seed,
        )
        delta = sum(differences) / len(differences) if differences else None
        paired_effects[label] = {
            "left": "A0_full",
            "right": baseline_id,
            "pairedCount": paired_count,
            "excludedInfraInvalidPairs": excluded_pairs,
            "delta": delta,
            "deltaPp": delta * 100 if delta is not None else None,
            "confidenceInterval95": {"low": ci_low, "high": ci_high},
            "bootstrapResamples": bootstrap_resamples,
        }

    return {
        "suite": "business",
        "sampleCount": len(materialized),
        "conditions": condition_summaries,
        "pairedEffects": paired_effects,
        "pairCount": len(pairs),
        "limitations": [
            "infra-invalid attempts are retained but excluded from gameplay success denominators",
            "paired effects describe the frozen task and route distribution only",
        ],
    }


# Friendly aliases used by CLI/report code and external notebooks.
aggregate_results = aggregate_paired_results
aggregate_business_results = aggregate_paired_results
score_business_attempt = score_attempt


__all__ = [
    "SuccessScore",
    "aggregate_business_results",
    "aggregate_paired_results",
    "aggregate_results",
    "paired_bootstrap_ci",
    "score_attempt",
    "score_business_attempt",
    "score_task_state",
]
