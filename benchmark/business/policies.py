"""Frozen decision policies used by the business benchmark.

The baseline policies deliberately operate on a very small interface: a state
mapping containing ``legalActions`` and, optionally, public goals/pending
action ids.  This keeps B0/B1/B2 independent from the production runtime and
lets the benchmark adapter pass a read-only public projection to them.  A0 is
an adapter around the production agent (or a deterministic plan in offline
pilot mode); it is not a second implementation of the world rules.
"""

from __future__ import annotations

import inspect
import random
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

Action = Mapping[str, Any]
ActionDecider = Callable[[Mapping[str, Any]], Action | Awaitable[Action]]


def _value(item: Any, *names: str, default: Any = None) -> Any:
    """Read either mapping keys or object attributes, accepting camelCase."""

    for name in names:
        if isinstance(item, Mapping) and name in item:
            return item[name]
        if hasattr(item, name):
            return getattr(item, name)
    return default


def action_id(action: Action) -> str:
    return str(_value(action, "id", "actionId", "action", default=""))


def action_kind(action: Action) -> str:
    return str(_value(action, "kind", "type", default=action_id(action) or "wait"))


def legal_actions(state: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Return a stable, normalized legal-action tuple from a state mapping."""

    raw = _value(state, "legalActions", "legal_actions", default=())
    if raw is None:
        return ()
    normalized: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, Mapping):
            normalized.append(dict(item))
        else:
            normalized.append({"id": str(item), "kind": str(item)})
    # The task catalog is frozen, but sorting makes a caller's list order
    # irrelevant to B1 and therefore prevents accidental baseline drift.
    return tuple(sorted(normalized, key=lambda value: (action_id(value), action_kind(value))))


def public_state_view(state: Mapping[str, Any]) -> dict[str, Any]:
    """Make the public-only input used by B2.

    Private facts and retrieval/context fields are removed rather than merely
    ignored by convention.  Nested public goals remain intact because they are
    part of the benchmark's ground-truth public projection.
    """

    private_keys = {
        "privateFacts",
        "private_facts",
        "coreSecrets",
        "core_secrets",
        "fullPlan",
        "full_plan",
        "pendingActionIds",
        "pending_action_ids",
        "appliedActionIds",
        "applied_action_ids",
        "memories",
        "memoryResults",
        "memory_results",
        "retrieval",
        "retrievedMemories",
        "retrieved_memories",
        "ownerMemory",
        "owner_memory",
    }
    result = {str(key): value for key, value in state.items() if str(key) not in private_keys}
    raw_actions = result.get("legalActions", result.get("legal_actions"))
    if isinstance(raw_actions, Sequence) and not isinstance(raw_actions, (str, bytes)):
        # Effects are the ground-truth implementation details of the offline
        # environment.  A baseline must choose using public affordances, not
        # inspect the answer encoded in an action's effect patch.
        public_actions: list[dict[str, Any]] = []
        public_keys = {"id", "actionId", "action_id", "kind", "type", "goalId", "goal_id", "targetActorId", "target_actor_id", "actorId", "actor_id", "priority"}
        for action in raw_actions:
            if isinstance(action, Mapping):
                public_actions.append({str(key): value for key, value in action.items() if str(key) in public_keys})
            else:
                public_actions.append({"id": str(action), "kind": str(action)})
        if "legalActions" in result:
            result["legalActions"] = public_actions
        else:
            result["legal_actions"] = public_actions
    return result


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """A policy decision plus provenance useful in per-case artifacts."""

    action: dict[str, Any]
    policy_id: str
    used_public_state: bool = True
    rationale: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": dict(self.action),
            "policyId": self.policy_id,
            "usedPublicState": self.used_public_state,
            "rationale": self.rationale,
            "metadata": dict(self.metadata),
        }


class DecisionPolicy(Protocol):
    """Minimal policy port consumed by :class:`BusinessTaskRunner`."""

    policy_id: str

    def choose(self, state: Mapping[str, Any]) -> PolicyDecision | Action:
        ...


class NoopPolicy:
    """B0: choose ``wait`` on every optional turn."""

    policy_id = "B0_noop"

    def choose(self, state: Mapping[str, Any]) -> PolicyDecision:
        actions = legal_actions(state)
        wait = next(
            (item for item in actions if action_id(item).lower() == "wait" or action_kind(item).lower() == "wait"),
            None,
        )
        if wait is None:
            # A malformed task must still produce an auditable legal decision;
            # the runner records the absence of wait as a baseline caveat.
            wait = {"id": "wait", "kind": "wait"}
        return PolicyDecision(
            action=dict(wait),
            policy_id=self.policy_id,
            rationale="always_wait",
            metadata={"waitAvailable": any(action_id(item) == "wait" for item in actions)},
        )


@dataclass(slots=True)
class RandomLegalPolicy:
    """B1: sample one legal action from a frozen kind-weight distribution."""

    seed: int = 0
    policy_id: str = "B1_random_legal"
    kind_weights: Mapping[str, float] = field(
        default_factory=lambda: {
            "wait": 0.40,
            "speak": 0.30,
            "commit": 0.12,
            "authorize": 0.10,
            "deliver": 0.05,
            "submit": 0.03,
        }
    )
    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)
        if any(float(weight) < 0 for weight in self.kind_weights.values()):
            raise ValueError("kind weights must be non-negative")

    def choose(self, state: Mapping[str, Any]) -> PolicyDecision:
        actions = legal_actions(state)
        if not actions:
            return PolicyDecision(
                action={"id": "wait", "kind": "wait"},
                policy_id=self.policy_id,
                rationale="no_legal_actions",
            )
        weights = [max(0.0, float(self.kind_weights.get(action_kind(item), 0.0))) for item in actions]
        if not any(weights):
            weights = [1.0] * len(actions)
        selected = self._rng.choices(actions, weights=weights, k=1)[0]
        return PolicyDecision(
            action=dict(selected),
            policy_id=self.policy_id,
            rationale="weighted_random_legal",
            metadata={
                "seed": self.seed,
                "distribution": dict(self.kind_weights),
                "opportunityNormalized": True,
            },
        )


@dataclass(slots=True)
class MyopicRulePolicy:
    """B2: pursue the nearest public goal, with no LLM or memory lookup."""

    policy_id: str = "B2_myopic_rule"

    def choose(self, state: Mapping[str, Any]) -> PolicyDecision:
        public = public_state_view(state)
        actions = legal_actions(public)
        if not actions:
            return PolicyDecision(
                action={"id": "wait", "kind": "wait"},
                policy_id=self.policy_id,
                rationale="no_legal_actions",
            )

        pending_ids = self._pending_action_ids(public)
        if pending_ids:
            for pending_id in pending_ids:
                match = next((item for item in actions if action_id(item) == pending_id), None)
                if match is not None:
                    return PolicyDecision(
                        action=dict(match),
                        policy_id=self.policy_id,
                        used_public_state=True,
                        rationale="next_public_goal_action",
                        metadata={"pendingActionId": pending_id},
                    )

        public_goals = self._public_goal_ids(public)
        candidates = [
            item
            for item in actions
            if _value(item, "goalId", "goal_id", default=None) in public_goals
            and action_kind(item) != "wait"
        ]
        if candidates:
            selected = min(
                candidates,
                key=lambda item: (
                    int(_value(item, "priority", default=0) or 0),
                    action_id(item),
                ),
            )
            return PolicyDecision(
                action=dict(selected),
                policy_id=self.policy_id,
                used_public_state=True,
                rationale="nearest_public_goal",
            )

        wait = next(
            (item for item in actions if action_kind(item).lower() == "wait" or action_id(item).lower() == "wait"),
            actions[0],
        )
        return PolicyDecision(
            action=dict(wait),
            policy_id=self.policy_id,
            used_public_state=True,
            rationale="no_immediate_public_goal_action",
        )

    @staticmethod
    def _pending_action_ids(state: Mapping[str, Any]) -> tuple[str, ...]:
        raw = _value(state, "pendingActionIds", "pending_action_ids", default=())
        if raw is None:
            return ()
        if isinstance(raw, str):
            return (raw,)
        return tuple(str(item) for item in raw)

    @staticmethod
    def _public_goal_ids(state: Mapping[str, Any]) -> set[str]:
        raw = _value(state, "publicGoals", "public_goals", default=())
        if raw is None:
            return set()
        if isinstance(raw, str):
            return {raw}
        ids: set[str] = set()
        for goal in raw:
            if isinstance(goal, Mapping):
                goal_id = _value(goal, "goalId", "goal_id", "id", default=None)
                status = str(_value(goal, "status", default="active"))
                if goal_id is not None and status not in {"achieved", "completed", "done"}:
                    ids.add(str(goal_id))
            else:
                ids.add(str(goal))
        return ids


@dataclass(slots=True)
class FullSystemPolicy:
    """A0 adapter for the production agent or a frozen offline action plan."""

    decider: ActionDecider | None = None
    plan: Sequence[str] = ()
    policy_id: str = "A0_full"
    _plan_cursor: int = field(default=0, init=False, repr=False)

    def choose(self, state: Mapping[str, Any]) -> PolicyDecision:
        if self.decider is not None:
            outcome = self.decider(state)
            if inspect.isawaitable(outcome):
                raise TypeError("async A0 decider requires await_choose()")
            return self._coerce_decision(outcome, "production_decider")

        configured_plan = tuple(self.plan)
        if not configured_plan:
            configured_plan = tuple(
                str(item)
                for item in _value(state, "fullPlan", "full_plan", default=())
            )
        actions = legal_actions(state)
        for action_name in configured_plan[self._plan_cursor :]:
            match = next((item for item in actions if action_id(item) == action_name), None)
            if match is not None:
                self._plan_cursor += configured_plan[self._plan_cursor :].index(action_name) + 1
                return PolicyDecision(
                    action=dict(match),
                    policy_id=self.policy_id,
                    rationale="frozen_full_plan",
                    metadata={"offlinePlan": True},
                )
        # A live adapter is expected to provide a decider.  The deterministic
        # fallback is only useful for an offline smoke run and remains visible
        # in metadata so it cannot be mistaken for a live result.
        fallback = MyopicRulePolicy().choose(state)
        return PolicyDecision(
            action=fallback.action,
            policy_id=self.policy_id,
            rationale="offline_public_goal_fallback",
            metadata={"offlinePlan": True, "fallback": "myopic_rule"},
        )

    async def await_choose(self, state: Mapping[str, Any]) -> PolicyDecision:
        if self.decider is None:
            return self.choose(state)
        outcome = self.decider(state)
        if inspect.isawaitable(outcome):
            outcome = await outcome
        return self._coerce_decision(outcome, "production_decider")

    def _coerce_decision(self, outcome: Action, rationale: str) -> PolicyDecision:
        if isinstance(outcome, PolicyDecision):
            return outcome
        if not isinstance(outcome, Mapping):
            raise TypeError("A0 decider must return an action mapping")
        # Accept a wrapper returned by adapters as well as a bare action.
        action = outcome.get("action") if isinstance(outcome.get("action"), Mapping) else outcome
        return PolicyDecision(
            action=dict(action),
            policy_id=self.policy_id,
            rationale=rationale,
            metadata={"offlinePlan": False},
        )


# Explicit aliases make the experiment manifest easy to read and provide a
# stable import surface for downstream runners.
B0NoopPolicy = NoopPolicy
B1RandomLegalPolicy = RandomLegalPolicy
B2MyopicRulePolicy = MyopicRulePolicy
A0FullPolicy = FullSystemPolicy


def build_policy(
    policy_id: str,
    *,
    seed: int = 0,
    decider: ActionDecider | None = None,
    plan: Sequence[str] = (),
) -> DecisionPolicy:
    """Construct a frozen policy by manifest condition id."""

    normalized = policy_id.strip()
    if normalized == "B0_noop":
        return NoopPolicy()
    if normalized == "B1_random_legal":
        return RandomLegalPolicy(seed=seed)
    if normalized == "B2_myopic_rule":
        return MyopicRulePolicy()
    if normalized == "A0_full":
        return FullSystemPolicy(decider=decider, plan=plan)
    raise ValueError(f"unknown business policy: {policy_id!r}")


__all__ = [
    "A0FullPolicy",
    "Action",
    "B0NoopPolicy",
    "B1RandomLegalPolicy",
    "B2MyopicRulePolicy",
    "DecisionPolicy",
    "FullSystemPolicy",
    "MyopicRulePolicy",
    "NoopPolicy",
    "PolicyDecision",
    "RandomLegalPolicy",
    "action_id",
    "action_kind",
    "build_policy",
    "legal_actions",
    "public_state_view",
]
