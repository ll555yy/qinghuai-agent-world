"""Paired offline reliability runner for the eight deterministic faults."""

from __future__ import annotations

import inspect
import json
import time
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .digest import state_digest
from .injectors import FaultInjector, FaultPlan, load_fault_plans


def _get(value: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in value:
            return value[name]
    return default


def _control_call(adapter: Any, seed: int, plan: FaultPlan) -> Any:
    method = adapter.run_control
    if "plan" in inspect.signature(method).parameters:
        return method(seed, plan=plan)
    return method(seed)


@dataclass(frozen=True, slots=True)
class ReliabilityObservation:
    """Adapter result for one control or fault run."""

    state: Any
    recovered: bool = True
    retry_count: int = 0
    side_effect_ids: tuple[str, ...] = ()
    rollback_correct: bool | None = None
    recovery_time_ms: float | None = None
    infra_valid: bool = True
    failure_code: str | None = None
    duplicate_side_effect: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_value(cls, value: Any) -> ReliabilityObservation:
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            raw_ids = _get(value, "sideEffectIds", "side_effect_ids", default=()) or ()
            if isinstance(raw_ids, str):
                raw_ids = (raw_ids,)
            metadata = _get(value, "metadata", default={}) or {}
            return cls(
                state=_get(value, "state", "snapshot", "finalState", "final_state", default={}),
                recovered=bool(_get(value, "recovered", "recoverySuccess", "recovery_success", default=True)),
                retry_count=int(_get(value, "retryCount", "retry_count", "retries", default=0)),
                side_effect_ids=tuple(str(item) for item in raw_ids),
                rollback_correct=_get(value, "rollbackCorrect", "rollback_correct", default=None),
                recovery_time_ms=_get(value, "recoveryTimeMs", "recovery_time_ms", default=None),
                infra_valid=bool(_get(value, "infraValid", "infra_valid", default=True)),
                failure_code=_get(value, "failureCode", "failure_code", default=None),
                duplicate_side_effect=bool(_get(value, "duplicateSideEffect", "duplicate_side_effect", default=False)),
                metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
            )
        return cls(state=value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "recovered": self.recovered,
            "retryCount": self.retry_count,
            "sideEffectIds": list(self.side_effect_ids),
            "rollbackCorrect": self.rollback_correct,
            "recoveryTimeMs": self.recovery_time_ms,
            "infraValid": self.infra_valid,
            "failureCode": self.failure_code,
            "duplicateSideEffect": self.duplicate_side_effect,
            "metadata": dict(self.metadata),
        }


@runtime_checkable
class FaultAwareReliabilityAdapter(Protocol):
    """Adapter boundary for the actual runtime under test."""

    def run_control(self, seed: int) -> ReliabilityObservation | Mapping[str, Any] | Any:
        ...

    def run_fault(self, plan: FaultPlan, seed: int, injector: FaultInjector) -> ReliabilityObservation | Mapping[str, Any] | Any:
        ...


class DeterministicReliabilityAdapter:
    """Small offline adapter proving the runner/injector contract.

    It models a transaction, durable events, and an idempotent command.  The
    output is tagged ``fixture`` and is intentionally not a production
    reliability claim.
    """

    def _base_state(self, seed: int) -> dict[str, Any]:
        return {
            "eventSeq": 3,
            "stateVersion": 3,
            "worldTime": {"day": 3, "hour": 18, "minute": 0},
            "goals": {"goal_venue": {"status": "pending", "progress": 1}},
            "relationships": {"npc_001/player_001": {"familiarity": 3}},
            "memories": {"memory_venue_001": {"ownerNpcId": "npc_001", "topic": "venue"}},
            "messages": [{"messageId": "msg_001", "text": "公开场地条件"}],
            "chapterState": {"resolution": "pending", "seed": seed},
        }

    def run_control(self, seed: int) -> ReliabilityObservation:
        return ReliabilityObservation(
            state=self._base_state(seed),
            recovered=True,
            retry_count=0,
            side_effect_ids=("command:venue:001",),
            rollback_correct=True,
            recovery_time_ms=0.0,
            metadata={"fixture": True, "executionMode": "offline_fixture"},
        )

    def run_fault(self, plan: FaultPlan, seed: int, injector: FaultInjector) -> ReliabilityObservation:
        state = self._base_state(seed)
        effects: set[str] = set()

        def idempotent_command() -> dict[str, Any]:
            effect_id = "command:venue:001"
            effects.add(effect_id)
            return {"ok": True, "effectId": effect_id}

        def restart() -> None:
            # The durable state is reconstructed unchanged; runtime locks and
            # in-flight handles are deliberately not represented in state.
            return None

        injector.restart_callback = restart
        try:
            injector.invoke_with_retries(
                idempotent_command,
                point=plan.injection_point,
                max_retries=3,
            )
            recovered = True
            failure_code = None
        except Exception as exc:  # noqa: BLE001 - benchmark records arbitrary adapter failures
            recovered = False
            failure_code = getattr(exc, "fault_id", type(exc).__name__)
        duplicate = len(effects) != len(set(effects))
        # Fault F6/F8 intentionally exercise the command twice, but an
        # idempotent command still contributes one side effect.
        return ReliabilityObservation(
            state=state,
            recovered=recovered,
            retry_count=injector.retries,
            side_effect_ids=tuple(sorted(effects)),
            rollback_correct=True,
            recovery_time_ms=1.0 + injector.retries,
            infra_valid=True,
            failure_code=failure_code,
            duplicate_side_effect=duplicate,
            metadata={
                "fixture": True,
                "executionMode": "offline_fixture",
                "fault": plan.fault_id,
                "injector": injector.snapshot(),
            },
        )


@dataclass(frozen=True, slots=True)
class FaultAttemptResult:
    fault_id: str
    seed: int
    attempt_index: int
    control_digest: str | None
    fault_digest: str | None
    recovered: bool
    state_diverged: bool
    duplicate_side_effect: bool
    retry_count: int
    retry_succeeded: bool
    rollback_correct: bool | None
    infra_valid: bool
    recovery_time_ms: float | None
    failure_code: str | None = None
    control: ReliabilityObservation | None = None
    fault: ReliabilityObservation | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "faultId": self.fault_id,
            "seed": self.seed,
            "attemptIndex": self.attempt_index,
            "controlDigest": self.control_digest,
            "faultDigest": self.fault_digest,
            "recovered": self.recovered,
            "stateDiverged": self.state_diverged,
            "duplicateSideEffect": self.duplicate_side_effect,
            "retryCount": self.retry_count,
            "retrySucceeded": self.retry_succeeded,
            "rollbackCorrect": self.rollback_correct,
            "infraValid": self.infra_valid,
            "recoveryTimeMs": self.recovery_time_ms,
            "failureCode": self.failure_code,
            "control": self.control.to_dict() if self.control is not None else None,
            "fault": self.fault.to_dict() if self.fault is not None else None,
        }


@dataclass(frozen=True, slots=True)
class ReliabilityAggregate:
    total_attempts: int
    recovery_successes: int
    state_divergences: int
    duplicate_side_effects: int
    retry_attempts: int
    retry_successes: int
    rollback_checks: int
    rollback_successes: int
    infra_invalid: int
    recovery_time_p50_ms: float | None
    recovery_time_p95_ms: float | None
    by_fault: Mapping[str, Mapping[str, Any]]

    @property
    def recovery_success_rate(self) -> float:
        return self.recovery_successes / self.total_attempts if self.total_attempts else 0.0

    @property
    def state_divergence_rate(self) -> float:
        return self.state_divergences / self.total_attempts if self.total_attempts else 0.0

    @property
    def duplicate_side_effect_rate(self) -> float:
        return self.duplicate_side_effects / self.total_attempts if self.total_attempts else 0.0

    @property
    def retry_success_rate(self) -> float | None:
        return self.retry_successes / self.retry_attempts if self.retry_attempts else None

    @property
    def rollback_correctness_rate(self) -> float | None:
        return self.rollback_successes / self.rollback_checks if self.rollback_checks else None

    @property
    def infra_invalid_rate(self) -> float:
        return self.infra_invalid / self.total_attempts if self.total_attempts else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "totalAttempts": self.total_attempts,
            "recoverySuccesses": self.recovery_successes,
            "recoverySuccessRate": self.recovery_success_rate,
            "stateDivergences": self.state_divergences,
            "stateDivergenceRate": self.state_divergence_rate,
            "duplicateSideEffects": self.duplicate_side_effects,
            "duplicateSideEffectRate": self.duplicate_side_effect_rate,
            "retryAttempts": self.retry_attempts,
            "retrySuccesses": self.retry_successes,
            "retrySuccessRate": self.retry_success_rate,
            "rollbackChecks": self.rollback_checks,
            "rollbackSuccesses": self.rollback_successes,
            "rollbackCorrectnessRate": self.rollback_correctness_rate,
            "infraInvalid": self.infra_invalid,
            "infraInvalidRate": self.infra_invalid_rate,
            "recoveryTimeP50Ms": self.recovery_time_p50_ms,
            "recoveryTimeP95Ms": self.recovery_time_p95_ms,
            "byFault": {key: dict(value) for key, value in self.by_fault.items()},
        }


def _percentile(values: Sequence[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * percent / 100
    lower, upper = int(position), int(position + 0.999999999)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


class ReliabilityRunner:
    def __init__(self, adapter: FaultAwareReliabilityAdapter | Any | None = None, *, execution_mode: str | None = None) -> None:
        self.adapter = adapter or DeterministicReliabilityAdapter()
        self.execution_mode = execution_mode or ("offline_fixture" if isinstance(self.adapter, DeterministicReliabilityAdapter) else "adapter")

    def run(self, plans: Sequence[FaultPlan] | None = None, *, seeds: Sequence[int] = tuple(range(10)), output_dir: str | Path | None = None) -> tuple[tuple[FaultAttemptResult, ...], ReliabilityAggregate]:
        plans = tuple(plans or load_fault_plans())
        if len(plans) != 8:
            raise ValueError(f"reliability runner requires exactly 8 fault plans, got {len(plans)}")
        if not seeds:
            raise ValueError("at least one paired seed is required")
        results: list[FaultAttemptResult] = []
        for plan in plans:
            for attempt_index, seed in enumerate(seeds, start=1):
                control: ReliabilityObservation | None = None
                fault: ReliabilityObservation | None = None
                failure_code: str | None = None
                started = time.perf_counter()
                try:
                    control = ReliabilityObservation.from_value(
                        _control_call(self.adapter, int(seed), plan)
                    )
                    injector = FaultInjector(plan, seed=int(seed))
                    fault = ReliabilityObservation.from_value(self.adapter.run_fault(plan, int(seed), injector))
                except Exception as exc:  # noqa: BLE001 - failure taxonomy must retain adapter errors
                    failure_code = getattr(exc, "fault_id", type(exc).__name__)
                    if fault is None:
                        fault = ReliabilityObservation(
                            state={},
                            recovered=False,
                            infra_valid=False,
                            failure_code=failure_code,
                            recovery_time_ms=(time.perf_counter() - started) * 1000,
                        )
                control_digest = state_digest(control.state) if control is not None else None
                fault_digest = state_digest(fault.state) if fault is not None else None
                state_diverged = control_digest != fault_digest
                effect_counts = Counter(fault.side_effect_ids) if fault is not None else Counter()
                duplicate = bool(fault and (fault.duplicate_side_effect or any(count > 1 for count in effect_counts.values())))
                retry_count = fault.retry_count if fault is not None else 0
                retry_succeeded = bool(fault and retry_count > 0 and fault.recovered)
                results.append(
                    FaultAttemptResult(
                        fault_id=plan.fault_id,
                        seed=int(seed),
                        attempt_index=attempt_index,
                        control_digest=control_digest,
                        fault_digest=fault_digest,
                        recovered=bool(fault and fault.recovered and fault.infra_valid),
                        state_diverged=state_diverged,
                        duplicate_side_effect=duplicate,
                        retry_count=retry_count,
                        retry_succeeded=retry_succeeded,
                        rollback_correct=fault.rollback_correct if fault is not None else None,
                        infra_valid=bool(fault and fault.infra_valid),
                        recovery_time_ms=fault.recovery_time_ms if fault is not None else None,
                        failure_code=failure_code or (fault.failure_code if fault else None),
                        control=control,
                        fault=fault,
                    )
                )
        aggregate = aggregate_reliability(results)
        if output_dir is not None:
            self._write_results(tuple(results), aggregate, Path(output_dir))
        return tuple(results), aggregate

    # A descriptive alias used by callers that think in terms of a suite.
    run_suite = run

    async def run_async(
        self,
        plans: Sequence[FaultPlan] | None = None,
        *,
        seeds: Sequence[int] = tuple(range(10)),
    ) -> tuple[tuple[FaultAttemptResult, ...], ReliabilityAggregate]:
        """Async production counterpart; the synchronous fixture API remains stable."""

        plans = tuple(plans or load_fault_plans())
        if len(plans) != 8:
            raise ValueError(f"reliability runner requires exactly 8 fault plans, got {len(plans)}")
        if not seeds:
            raise ValueError("at least one paired seed is required")
        results: list[FaultAttemptResult] = []
        for plan in plans:
            for attempt_index, seed in enumerate(seeds, start=1):
                control: ReliabilityObservation | None = None
                fault: ReliabilityObservation | None = None
                failure_code: str | None = None
                started = time.perf_counter()
                try:
                    control_value = _control_call(self.adapter, int(seed), plan)
                    if inspect.isawaitable(control_value):
                        control_value = await control_value
                    control = ReliabilityObservation.from_value(control_value)
                    injector = FaultInjector(plan, seed=int(seed))
                    fault_value = self.adapter.run_fault(plan, int(seed), injector)
                    if inspect.isawaitable(fault_value):
                        fault_value = await fault_value
                    fault = ReliabilityObservation.from_value(fault_value)
                except Exception as exc:  # noqa: BLE001 - retain arbitrary adapter failures
                    failure_code = getattr(exc, "fault_id", type(exc).__name__)
                    if fault is None:
                        fault = ReliabilityObservation(
                            state={},
                            recovered=False,
                            infra_valid=False,
                            failure_code=failure_code,
                            recovery_time_ms=(time.perf_counter() - started) * 1000,
                        )
                results.append(
                    self._paired_result(
                        plan,
                        int(seed),
                        attempt_index,
                        control,
                        fault,
                        failure_code,
                    )
                )
        result_tuple = tuple(results)
        return result_tuple, aggregate_reliability(result_tuple)

    @staticmethod
    def _paired_result(
        plan: FaultPlan,
        seed: int,
        attempt_index: int,
        control: ReliabilityObservation | None,
        fault: ReliabilityObservation | None,
        failure_code: str | None,
    ) -> FaultAttemptResult:
        control_digest = state_digest(control.state) if control is not None else None
        fault_digest = state_digest(fault.state) if fault is not None else None
        effect_counts = Counter(fault.side_effect_ids) if fault is not None else Counter()
        duplicate = bool(
            fault
            and (
                fault.duplicate_side_effect
                or any(count > 1 for count in effect_counts.values())
            )
        )
        retry_count = fault.retry_count if fault is not None else 0
        return FaultAttemptResult(
            fault_id=plan.fault_id,
            seed=seed,
            attempt_index=attempt_index,
            control_digest=control_digest,
            fault_digest=fault_digest,
            recovered=bool(fault and fault.recovered and fault.infra_valid),
            state_diverged=control_digest != fault_digest,
            duplicate_side_effect=duplicate,
            retry_count=retry_count,
            retry_succeeded=bool(fault and retry_count > 0 and fault.recovered),
            rollback_correct=fault.rollback_correct if fault is not None else None,
            infra_valid=bool(fault and fault.infra_valid),
            recovery_time_ms=fault.recovery_time_ms if fault is not None else None,
            failure_code=failure_code or (fault.failure_code if fault else None),
            control=control,
            fault=fault,
        )

    @staticmethod
    def _write_results(results: tuple[FaultAttemptResult, ...], aggregate: ReliabilityAggregate, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "raw-traces").mkdir(exist_ok=True)
        with (output_dir / "per-case.jsonl").open("w", encoding="utf-8") as handle:
            for result in results:
                handle.write(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True, default=str) + "\n")
        (output_dir / "aggregate.json").write_text(json.dumps(aggregate.to_dict(), ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")
        (output_dir / "README.md").write_text(
            "# Reliability benchmark results\n\n"
            "This directory contains paired control/fault observations. Offline fixture output is not live-provider evidence.\n",
            encoding="utf-8",
        )


def aggregate_reliability(results: Sequence[FaultAttemptResult]) -> ReliabilityAggregate:
    by_fault: dict[str, list[FaultAttemptResult]] = defaultdict(list)
    for result in results:
        by_fault[result.fault_id].append(result)
    recovery_times = [result.recovery_time_ms for result in results if result.recovery_time_ms is not None]
    fault_summary: dict[str, Mapping[str, Any]] = {}
    for fault_id, rows in sorted(by_fault.items()):
        fault_summary[fault_id] = {
            "attempts": len(rows),
            "recoverySuccesses": sum(row.recovered for row in rows),
            "recoverySuccessRate": sum(row.recovered for row in rows) / len(rows) if rows else 0.0,
            "stateDivergences": sum(row.state_diverged for row in rows),
            "duplicateSideEffects": sum(row.duplicate_side_effect for row in rows),
            "retryAttempts": sum(row.retry_count > 0 for row in rows),
            "retrySuccesses": sum(row.retry_succeeded for row in rows),
            "infraInvalid": sum(not row.infra_valid for row in rows),
        }
    return ReliabilityAggregate(
        total_attempts=len(results),
        recovery_successes=sum(result.recovered for result in results),
        state_divergences=sum(result.state_diverged for result in results),
        duplicate_side_effects=sum(result.duplicate_side_effect for result in results),
        retry_attempts=sum(result.retry_count > 0 for result in results),
        retry_successes=sum(result.retry_succeeded for result in results),
        rollback_checks=sum(result.rollback_correct is not None for result in results),
        rollback_successes=sum(result.rollback_correct is True for result in results),
        infra_invalid=sum(not result.infra_valid for result in results),
        recovery_time_p50_ms=_percentile(recovery_times, 50),
        recovery_time_p95_ms=_percentile(recovery_times, 95),
        by_fault=fault_summary,
    )


def run_reliability_suite(*, plans: Sequence[FaultPlan] | None = None, seeds: Sequence[int] = tuple(range(10)), adapter: Any | None = None, output_dir: str | Path | None = None, execution_mode: str | None = None) -> tuple[tuple[FaultAttemptResult, ...], ReliabilityAggregate]:
    return ReliabilityRunner(adapter, execution_mode=execution_mode).run(plans, seeds=seeds, output_dir=output_dir)


def run_reliability_benchmark(*, plans: Sequence[FaultPlan] | None = None, seeds: Sequence[int] = tuple(range(10)), adapter: Any | None = None, output_dir: str | Path | None = None, execution_mode: str | None = None) -> tuple[tuple[FaultAttemptResult, ...], ReliabilityAggregate]:
    return run_reliability_suite(plans=plans, seeds=seeds, adapter=adapter, output_dir=output_dir, execution_mode=execution_mode)


__all__ = [
    "DeterministicReliabilityAdapter",
    "FaultAttemptResult",
    "FaultAwareReliabilityAdapter",
    "ReliabilityAggregate",
    "ReliabilityObservation",
    "ReliabilityRunner",
    "aggregate_reliability",
    "run_reliability_benchmark",
    "run_reliability_suite",
]
