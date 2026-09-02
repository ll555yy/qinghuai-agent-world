"""Task catalog loader and deterministic business benchmark runner.

``BusinessTaskRunner`` intentionally has two modes:

* the default offline environment applies the frozen task effects and is used
  for CI/pilot checks; and
* a caller can provide ``step_adapter`` to send the same action ids to the
  production world engine.  The adapter is responsible for returning the
  authoritative state projection; the runner still owns attempt bookkeeping.

The runner never decides success from generated text.  It delegates that to
``business.scorer`` over the state returned by the environment/adapter.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any

import yaml

from .policies import (
    Action,
    FullSystemPolicy,
    PolicyDecision,
    action_id,
    build_policy,
    legal_actions,
    public_state_view,
)
from .scorer import SuccessScore, score_task_state

DEFAULT_TASKS_PATH = Path(__file__).with_name("tasks.yaml")
POLICY_IDS = ("B0_noop", "B1_random_legal", "B2_myopic_rule", "A0_full")
ROUTES = ("autonomous", "player_assisted")

StepAdapter = Callable[
    [Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]],
    Mapping[str, Any] | Awaitable[Mapping[str, Any]],
]
TraceSink = Callable[[Mapping[str, Any]], Any]


def canonical_json(value: Any) -> str:
    """Encode nested benchmark data deterministically for digests."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def state_digest(state: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(state).encode("utf-8")).hexdigest()


def _field(value: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


def _snake_case(name: str) -> str:
    chars: list[str] = []
    for char in name:
        if char.isupper():
            chars.extend(("_", char.lower()))
        else:
            chars.append(char)
    return "".join(chars).lstrip("_")


def _camel_case(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


def _common_model(name: str) -> Any | None:
    """Resolve the parent benchmark contract lazily.

    The business package is testable before ``benchmark.common`` is installed,
    while live integration uses the shared ``ExperimentManifest``,
    ``AttemptRecord`` and ``TelemetryRecord`` classes when available.
    """

    try:
        from benchmark.common import models as common_models
    except (ImportError, ModuleNotFoundError):
        try:
            from benchmark.common import (  # type: ignore[attr-defined]
                AttemptRecord,
                ExperimentManifest,
                TelemetryRecord,
            )
        except (ImportError, ModuleNotFoundError):
            return None
        return {"AttemptRecord": AttemptRecord, "ExperimentManifest": ExperimentManifest, "TelemetryRecord": TelemetryRecord}.get(name)
    return getattr(common_models, name, None)


def _instantiate_common(name: str, payload: Mapping[str, Any]) -> Any:
    """Instantiate a shared record without coupling to field naming style."""

    cls = _common_model(name)
    if cls is None:
        return dict(payload)

    try:
        import inspect as _inspect

        signature = _inspect.signature(cls)
        accepted: dict[str, Any] = {}
        accepts_kwargs = any(
            parameter.kind is parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        for key, value in payload.items():
            candidates = (key, _snake_case(key), _camel_case(key))
            selected = next((candidate for candidate in candidates if candidate in signature.parameters), None)
            if selected is not None:
                accepted[selected] = value
            elif accepts_kwargs:
                accepted[key] = value
        try:
            return cls(**accepted)
        except TypeError:
            # Pydantic models can use aliases not exposed as Python signature
            # parameters; model_validate accepts the canonical wire shape.
            validator = getattr(cls, "model_validate", None)
            if validator is not None:
                return validator(dict(payload))
            return dict(payload)
    except (TypeError, ValueError):
        validator = getattr(cls, "model_validate", None)
        if validator is not None:
            try:
                return validator(dict(payload))
            except (TypeError, ValueError, AttributeError):  # pragma: no cover - adapter boundary
                return dict(payload)
        return dict(payload)


def _record_mapping(value: Any) -> dict[str, Any]:
    data = _jsonable(value)
    return dict(data) if isinstance(data, Mapping) else {"value": data}


def _nested_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _copy_state(value: Mapping[str, Any]) -> dict[str, Any]:
    # JSON round-trip is sufficient for frozen YAML task state and prevents an
    # attempt from mutating the catalog shared by paired runs.
    return json.loads(canonical_json(value))


@dataclass(frozen=True, slots=True)
class BusinessTask:
    task_id: str
    category: str
    title: str
    description: str
    raw: Mapping[str, Any]

    @property
    def max_steps(self) -> int:
        return int(self.raw.get("maxSteps", 12))

    @property
    def full_plan(self) -> tuple[str, ...]:
        return tuple(str(item) for item in self.raw.get("fullPlan", ()))

    @property
    def initial_state(self) -> Mapping[str, Any]:
        state = _copy_state(_nested_dict(self.raw.get("initialState")))
        state.setdefault("taskId", self.task_id)
        state.setdefault("task_id", self.task_id)
        state.setdefault("worldDay", 1)
        state.setdefault("authorization", {})
        state.setdefault("commitments", {})
        state.setdefault("relationships", {})
        state.setdefault("chapter", {})
        return state

    @property
    def actions(self) -> tuple[dict[str, Any], ...]:
        raw_actions = self.raw.get("legalActions", ())
        actions: list[dict[str, Any]] = []
        for raw_action in raw_actions:
            if isinstance(raw_action, Mapping):
                actions.append(dict(raw_action))
        if not any(str(item.get("id", "")) == "wait" for item in actions):
            actions.append({"id": "wait", "kind": "wait", "goalId": None})
        return tuple(actions)

    def route_script(self, route: str) -> tuple[str, ...]:
        routes = self.raw.get("routes", {})
        config = routes.get(route, {}) if isinstance(routes, Mapping) else {}
        script = config.get("playerScript", ()) if isinstance(config, Mapping) else ()
        return tuple(str(item) for item in script)


def load_tasks(path: str | Path = DEFAULT_TASKS_PATH) -> tuple[BusinessTask, ...]:
    """Load and validate the frozen 12-task catalog."""

    with Path(path).open("r", encoding="utf-8") as handle:
        document = yaml.safe_load(handle) or {}
    tasks = document.get("tasks", [])
    if not isinstance(tasks, list):
        raise TypeError("business task catalog must contain a list under tasks")
    parsed: list[BusinessTask] = []
    seen: set[str] = set()
    for raw in tasks:
        if not isinstance(raw, Mapping):
            raise TypeError("each business task must be a mapping")
        task_id = str(raw.get("taskId", ""))
        category = str(raw.get("category", ""))
        if not task_id or task_id in seen:
            raise ValueError(f"duplicate or missing taskId: {task_id!r}")
        if category not in {"permit_commitment", "cross_day_commitment", "information_asymmetry", "conflict_repair"}:
            raise ValueError(f"unknown business task category: {category!r}")
        if not raw.get("success"):
            raise ValueError(f"task {task_id!r} has no success contract")
        if not raw.get("fullPlan"):
            raise ValueError(f"task {task_id!r} has no fullPlan")
        action_ids = {str(item.get("id")) for item in raw.get("legalActions", ()) if isinstance(item, Mapping)}
        missing = {str(item) for item in raw["fullPlan"]} - action_ids
        if missing:
            raise ValueError(f"task {task_id!r} fullPlan references unknown actions: {sorted(missing)}")
        routes = raw.get("routes", {})
        if not isinstance(routes, Mapping) or any(route not in routes for route in ROUTES):
            raise ValueError(f"task {task_id!r} must define autonomous and player_assisted routes")
        seen.add(task_id)
        parsed.append(
            BusinessTask(
                task_id=task_id,
                category=category,
                title=str(raw.get("title", task_id)),
                description=str(raw.get("description", "")),
                raw=raw,
            )
        )
    if len(parsed) != 12:
        raise ValueError(f"business catalog must contain exactly 12 tasks, got {len(parsed)}")
    expected_counts = {
        "permit_commitment": 4,
        "cross_day_commitment": 3,
        "information_asymmetry": 3,
        "conflict_repair": 2,
    }
    counts: dict[str, int] = {}
    for task in parsed:
        counts[task.category] = counts.get(task.category, 0) + 1
    if counts != expected_counts:
        raise ValueError(f"business category counts differ from frozen design: {counts}")
    return tuple(parsed)


@dataclass(slots=True)
class TaskEnvironment:
    task: BusinessTask
    route: str
    state: dict[str, Any] = field(init=False)
    applied_action_ids: list[str] = field(default_factory=list, init=False)
    traces: list[dict[str, Any]] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        if self.route not in ROUTES:
            raise ValueError(f"unknown business route: {self.route!r}")
        self.state = self.task.initial_state
        self.state["route"] = self.route
        self.state["fullPlan"] = list(self.task.full_plan)

    @property
    def action_by_id(self) -> dict[str, dict[str, Any]]:
        return {str(item.get("id")): item for item in self.task.actions}

    def view(self) -> dict[str, Any]:
        # Only this projection is passed to a DecisionPolicy or live adapter.
        # The private canonical action effects and the full plan stay inside
        # the environment and are used solely for deterministic state apply.
        result = public_state_view(_copy_state(self.state))
        available: list[dict[str, Any]] = []
        for action in self.task.actions:
            item_id = str(action.get("id", ""))
            if item_id in self.applied_action_ids and item_id != "wait":
                continue
            public_action = public_state_view({"legalActions": [dict(action)]})["legalActions"][0]
            available.append(dict(public_action))
        result["legalActions"] = available
        result["step"] = len(self.traces) + 1
        return result

    def apply(self, action: Mapping[str, Any], *, source: str = "policy") -> dict[str, Any]:
        item_id = str(action.get("id", action.get("actionId", action.get("action", ""))))
        canonical = self.action_by_id.get(item_id)
        if canonical is None:
            raise ValueError(f"action {item_id!r} is not in the task legal action catalog")
        if item_id in self.applied_action_ids and item_id != "wait":
            raise ValueError(f"action {item_id!r} cannot be applied twice")

        effects = canonical.get("effects", {})
        if isinstance(effects, Mapping):
            self._apply_effects(effects)
        if item_id != "wait":
            self.applied_action_ids.append(item_id)
        if source != "player":
            self.applied_action_ids = list(dict.fromkeys(self.applied_action_ids))
        self.state["worldDay"] = int(self.state.get("worldDay", 1)) + 1
        event = {
            "step": len(self.traces) + 1,
            "actionId": item_id,
            "kind": canonical.get("kind", "unknown"),
            "source": source,
            "targetActorId": canonical.get("targetActorId"),
            "worldDay": self.state["worldDay"],
        }
        self.traces.append(event)
        return event

    def _apply_effects(self, effects: Mapping[str, Any]) -> None:
        for key in ("authorization", "commitments", "chapter"):
            patch = effects.get(key)
            if isinstance(patch, Mapping):
                current = self.state.setdefault(key, {})
                if isinstance(current, Mapping):
                    current.update(_copy_state(patch))
        relationship_patch = effects.get("relationships")
        if isinstance(relationship_patch, Mapping):
            relationships = self.state.setdefault("relationships", {})
            if not isinstance(relationships, dict):
                relationships = {}
                self.state["relationships"] = relationships
            for relationship_key, value in relationship_patch.items():
                if isinstance(value, Mapping):
                    existing = relationships.setdefault(str(relationship_key), {})
                    if isinstance(existing, Mapping):
                        existing.update(_copy_state(value))
        if "goals" in effects and isinstance(effects["goals"], Mapping):
            goals = self.state.setdefault("goals", {})
            if isinstance(goals, dict):
                goals.update(_copy_state(effects["goals"]))


@dataclass(slots=True)
class BusinessRunResult:
    task: BusinessTask
    condition_id: str
    route: str
    seed: int
    paired_group_id: str
    attempt_index: int
    status: str
    infra_valid: bool
    gameplay_success: bool
    failure_type: str | None
    steps: int
    state: dict[str, Any]
    score: SuccessScore
    trace: list[dict[str, Any]]
    telemetry: Any
    attempt: Any
    duration_ms: float

    @property
    def task_id(self) -> str:
        return self.task.task_id

    @property
    def policy_id(self) -> str:
        return self.condition_id

    def as_dict(self) -> dict[str, Any]:
        return {
            "taskId": self.task_id,
            "conditionId": self.condition_id,
            "route": self.route,
            "seed": self.seed,
            "pairedGroupId": self.paired_group_id,
            "attemptIndex": self.attempt_index,
            "status": self.status,
            "infraValid": self.infra_valid,
            "gameplaySuccess": self.gameplay_success,
            "failureType": self.failure_type,
            "steps": self.steps,
            "state": _jsonable(self.state),
            "score": self.score.as_dict(),
            "trace": _jsonable(self.trace),
            "telemetry": _record_mapping(self.telemetry),
            "attempt": _record_mapping(self.attempt),
            "durationMs": self.duration_ms,
        }


class BusinessTaskRunner:
    """Execute business tasks against the offline environment or an adapter."""

    def __init__(
        self,
        tasks_path: str | Path = DEFAULT_TASKS_PATH,
        *,
        experiment_id: str = "business_p0_v1",
        attempt_index: int = 1,
        step_adapter: StepAdapter | None = None,
        trace_sink: TraceSink | None = None,
    ) -> None:
        self.tasks = load_tasks(tasks_path)
        self._tasks_by_id = {task.task_id: task for task in self.tasks}
        self.experiment_id = experiment_id
        self.attempt_index = attempt_index
        self.step_adapter = step_adapter
        self.trace_sink = trace_sink

    def get_task(self, task_id: str) -> BusinessTask:
        try:
            return self._tasks_by_id[task_id]
        except KeyError as exc:
            raise KeyError(f"unknown business task: {task_id!r}") from exc

    async def run_task(
        self,
        task_id: str,
        condition_id: str,
        *,
        seed: int = 0,
        route: str = "autonomous",
        paired_group_id: str | None = None,
        attempt_index: int | None = None,
        decider: Callable[[Mapping[str, Any]], Action | Awaitable[Action]] | None = None,
    ) -> BusinessRunResult:
        if condition_id not in POLICY_IDS:
            raise ValueError(f"unknown business condition: {condition_id!r}")
        if route not in ROUTES:
            raise ValueError(f"unknown business route: {route!r}")
        task = self.get_task(task_id)
        paired_id = paired_group_id or f"{task_id}:{route}:seed-{seed}"
        resolved_attempt_index = self.attempt_index if attempt_index is None else attempt_index
        started = time.perf_counter()
        live_telemetry = getattr(decider, "telemetry", None)
        live_before = {
            name: getattr(live_telemetry, name, 0) or 0
            for name in (
                "candidate_calls",
                "candidate_physical_requests",
                "candidate_retries",
                "prompt_tokens",
                "completion_tokens",
                "afp_used",
            )
        } if live_telemetry is not None else {}
        environment = TaskEnvironment(task, route)
        policy = build_policy(condition_id, seed=seed, decider=decider, plan=task.full_plan)
        infra_valid = True
        failure_type: str | None = None

        # Player-assisted routes use only the immutable script declared by the
        # task.  Its steps are explicit world actions and remain in the trace.
        for script_action_id in task.route_script(route):
            action = environment.action_by_id.get(script_action_id)
            if action is None:
                infra_valid = False
                failure_type = "route_script_invalid"
                break
            try:
                if self.step_adapter is not None:
                    adapted = self.step_adapter(task.raw, environment.view(), action)
                    if inspect.isawaitable(adapted):
                        adapted = await adapted
                    event = environment.apply(action, source="player")
                    if isinstance(adapted, Mapping):
                        authoritative = adapted.get("state", adapted)
                        if isinstance(authoritative, Mapping):
                            environment.state = _copy_state(authoritative)
                    environment.traces[-1].update({"adapter": True})
                else:
                    environment.apply(action, source="player")
            except (ValueError, TypeError, KeyError):
                infra_valid = False
                failure_type = "route_script_apply_error"
                break

        policy_calls = 0
        invalid_actions = 0
        if infra_valid:
            for _ in range(task.max_steps):
                state = environment.view()
                try:
                    if isinstance(policy, FullSystemPolicy) and policy.decider is not None:
                        decision = await policy.await_choose(state)
                    else:
                        decision = policy.choose(state)
                    if not isinstance(decision, PolicyDecision):
                        decision = PolicyDecision(action=dict(decision), policy_id=condition_id)
                    policy_calls += 1
                    action = decision.action
                    item_id = action_id(action)
                    legal = {action_id(item) for item in legal_actions(state)}
                    if item_id not in legal:
                        invalid_actions += 1
                        failure_type = "invalid_action"
                        event = {
                            "step": len(environment.traces) + 1,
                            "actionId": item_id,
                            "policyId": condition_id,
                            "valid": False,
                            "rationale": decision.rationale,
                        }
                        environment.traces.append(event)
                        if self.trace_sink is not None:
                            self.trace_sink(event)
                        break
                    before_state = environment.view()
                    adapted: Mapping[str, Any] | None = None
                    if self.step_adapter is not None:
                        candidate = self.step_adapter(task.raw, before_state, action)
                        if inspect.isawaitable(candidate):
                            candidate = await candidate
                        if not isinstance(candidate, Mapping):
                            raise TypeError("step_adapter must return a state mapping")
                        adapted = candidate
                    event = environment.apply(action)
                    if adapted is not None:
                        authoritative = adapted.get("state", adapted)
                        if not isinstance(authoritative, Mapping):
                            raise TypeError("step_adapter state must be a mapping")
                        environment.state = _copy_state(authoritative)
                        event["adapter"] = True
                    event.update(
                        {
                            "policyId": condition_id,
                            "valid": True,
                            "rationale": decision.rationale,
                            "metadata": decision.metadata,
                        }
                    )
                    if self.trace_sink is not None:
                        self.trace_sink(event)
                    score = score_task_state(task.raw, environment.state, environment.traces)
                    if score.success:
                        break
                except Exception as exc:  # noqa: BLE001 - provider/adapter boundary
                    infra_valid = False
                    failure_type = f"adapter_{type(exc).__name__}"
                    break

        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        final_state = environment.state
        score = score_task_state(task.raw, final_state, environment.traces)
        if not infra_valid:
            status = "infra-invalid"
            gameplay_success = False
        elif score.success:
            status = "completed"
            gameplay_success = True
            failure_type = None
        else:
            status = "failed"
            gameplay_success = False
            failure_type = failure_type or score.failure_type or (
                "max_steps" if len(environment.traces) >= task.max_steps else "goal_incomplete"
            )

        telemetry_payload = {
            "experimentId": self.experiment_id,
            "taskId": task.task_id,
            "conditionId": condition_id,
            "route": route,
            "seed": seed,
            "pairedGroupId": paired_id,
            "attemptIndex": resolved_attempt_index,
            "modelCalls": policy_calls if condition_id == "A0_full" and decider is not None else 0,
            "calls": policy_calls,
            "candidateCalls": policy_calls if condition_id == "A0_full" and decider is not None else 0,
            "embeddingCalls": 0,
            "inputTokens": None,
            "outputTokens": None,
            "totalTokens": None,
            "afp": None,
            "costCnyEstimated": None,
            "stepLatencyMs": [duration_ms / max(1, len(environment.traces))],
            "durationMs": duration_ms,
            "retryCount": 0,
            "invalidActionCount": invalid_actions,
            "infraValid": infra_valid,
        }
        if live_telemetry is not None:
            telemetry_payload.update(
                {
                    "candidateCalls": int(live_telemetry.candidate_calls - live_before["candidate_calls"]),
                    "modelCalls": int(live_telemetry.candidate_calls - live_before["candidate_calls"]),
                    "inputTokens": int(live_telemetry.prompt_tokens - live_before["prompt_tokens"]),
                    "outputTokens": int(live_telemetry.completion_tokens - live_before["completion_tokens"]),
                    "totalTokens": int(
                        live_telemetry.prompt_tokens
                        + live_telemetry.completion_tokens
                        - live_before["prompt_tokens"]
                        - live_before["completion_tokens"]
                    ),
                    "afp": float((live_telemetry.afp_used or 0) - live_before["afp_used"]),
                    "retryCount": int(live_telemetry.candidate_retries - live_before["candidate_retries"]),
                }
            )
        # ``benchmark.common.models`` currently uses snake_case dataclass
        # fields.  Keep the camelCase wire fields above for JSON artifacts and
        # add the canonical aliases below so AttemptRecord/TelemetryRecord are
        # real shared records when the common package is present.
        telemetry_payload.update(
            {
                "candidate_calls": telemetry_payload["candidateCalls"],
                "candidate_physical_requests": (
                    int(live_telemetry.candidate_physical_requests - live_before["candidate_physical_requests"])
                    if live_telemetry is not None
                    else telemetry_payload["candidateCalls"]
                ),
                "candidate_retries": telemetry_payload["retryCount"],
                "embedding_calls": telemetry_payload["embeddingCalls"],
                "embedding_physical_requests": 0,
                "embedding_retries": 0,
                "prompt_tokens": telemetry_payload["inputTokens"] or 0,
                "completion_tokens": telemetry_payload["outputTokens"] or 0,
                "embedding_tokens": 0,
                "afp_used": telemetry_payload["afp"],
                "cost_cny_estimated": telemetry_payload["costCnyEstimated"],
                "step_latencies_ms": telemetry_payload["stepLatencyMs"],
                "retrieval_latencies_ms": [],
                "run_duration_ms": telemetry_payload["durationMs"],
                "failures": {},
            }
        )
        telemetry = _instantiate_common("TelemetryRecord", telemetry_payload)
        common_status = status.replace("-", "_")
        try:
            from benchmark.common.models import AttemptStatus

            common_status_value: Any = AttemptStatus(common_status)
        except (ImportError, ModuleNotFoundError, ValueError):
            common_status_value = common_status
        attempt_payload = {
            "experimentId": self.experiment_id,
            "taskId": task.task_id,
            "caseId": task.task_id,
            "conditionId": condition_id,
            "baselineId": condition_id if condition_id.startswith("B") else None,
            "route": route,
            "seed": seed,
            "pairedGroupId": paired_id,
            "attemptIndex": resolved_attempt_index,
            "status": common_status_value,
            "infraValid": infra_valid,
            "gameplaySuccess": gameplay_success,
            "failureType": failure_type,
            "stepCount": len(environment.traces),
            "completionRounds": len(environment.traces),
            "stateDigest": state_digest(final_state),
            "metrics": score.as_dict(),
            "telemetry": telemetry,
            "trace": environment.traces,
            "details": {
                "taskId": task.task_id,
                "state": final_state,
                "trace": environment.traces,
                "score": score.as_dict(),
                "policyId": condition_id,
            },
        }
        attempt_payload.update(
            {
                "experiment_id": self.experiment_id,
                "case_id": task.task_id,
                "condition_id": condition_id,
                "seed": seed,
                "paired_group_id": paired_id,
                "attempt_index": resolved_attempt_index,
                "route": route,
                "status": common_status_value,
                "infra_valid": infra_valid,
                "gameplay_success": gameplay_success,
                "completion_rounds": len(environment.traces),
                "failure_type": failure_type,
                "telemetry": telemetry,
                "details": {
                    "taskId": task.task_id,
                    "state": final_state,
                    "trace": environment.traces,
                    "score": score.as_dict(),
                    "policyId": condition_id,
                },
            }
        )
        attempt = _instantiate_common("AttemptRecord", attempt_payload)
        return BusinessRunResult(
            task=task,
            condition_id=condition_id,
            route=route,
            seed=seed,
            paired_group_id=paired_id,
            attempt_index=resolved_attempt_index,
            status=status,
            infra_valid=infra_valid,
            gameplay_success=gameplay_success,
            failure_type=failure_type,
            steps=len(environment.traces),
            state=_copy_state(final_state),
            score=score,
            trace=list(environment.traces),
            telemetry=telemetry,
            attempt=attempt,
            duration_ms=duration_ms,
        )

    def run_task_sync(self, *args: Any, **kwargs: Any) -> BusinessRunResult:
        return asyncio.run(self.run_task(*args, **kwargs))

    async def run_matrix(
        self,
        *,
        conditions: Sequence[str] = POLICY_IDS,
        seeds: Sequence[int] = (0, 1, 2),
        routes: Sequence[str] = ROUTES,
        task_ids: Sequence[str] | None = None,
        decider: Callable[[Mapping[str, Any]], Action | Awaitable[Action]] | None = None,
    ) -> list[BusinessRunResult]:
        selected_tasks = tuple(task_ids) if task_ids is not None else tuple(self._tasks_by_id)
        results: list[BusinessRunResult] = []
        for task_id in selected_tasks:
            for route in routes:
                for seed in seeds:
                    paired_group_id = f"{task_id}:{route}:seed-{seed}"
                    for condition_id in conditions:
                        results.append(
                            await self.run_task(
                                task_id,
                                condition_id,
                                seed=seed,
                                route=route,
                                paired_group_id=paired_group_id,
                                decider=decider,
                            )
                        )
        return results

    def run_matrix_sync(self, **kwargs: Any) -> list[BusinessRunResult]:
        return asyncio.run(self.run_matrix(**kwargs))


__all__ = [
    "DEFAULT_TASKS_PATH",
    "POLICY_IDS",
    "ROUTES",
    "BusinessRunResult",
    "BusinessTask",
    "BusinessTaskRunner",
    "TaskEnvironment",
    "canonical_json",
    "load_tasks",
    "state_digest",
]
