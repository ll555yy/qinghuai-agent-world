from __future__ import annotations

import pytest

from benchmark.reliability import (
    FaultInjector,
    LostResponseFault,
    load_fault_plans,
    run_reliability_suite,
)


def test_frozen_fault_matrix_has_eight_plans_and_ten_attempts() -> None:
    plans = load_fault_plans()
    assert len(plans) == 8
    assert all(plan.attempts == 10 for plan in plans)
    assert [plan.fault_id for plan in plans] == [
        "F1_model_timeout",
        "F2_invalid_schema",
        "F3_embedding_outage",
        "F4_database_disconnect",
        "F5_process_restart",
        "F6_duplicate_command",
        "F7_ws_reconnect",
        "F8_lost_response",
    ]


def test_offline_runner_pairs_each_fault_with_same_seed_control() -> None:
    results, aggregate = run_reliability_suite(seeds=range(10))
    assert len(results) == 80
    assert aggregate.total_attempts == 80
    assert aggregate.recovery_success_rate == 1.0
    assert aggregate.state_divergence_rate == 0.0
    assert aggregate.duplicate_side_effect_rate == 0.0
    assert aggregate.infra_invalid_rate == 0.0
    assert all(row.control_digest == row.fault_digest for row in results)
    assert all(len([row for row in results if row.fault_id == fault_id]) == 10 for fault_id in {row.fault_id for row in results})


def test_lost_response_wrapper_commits_once_then_allows_idempotent_retry() -> None:
    plan = next(plan for plan in load_fault_plans() if plan.fault_id == "F8_lost_response")
    injector = FaultInjector(plan)
    calls = 0

    def operation() -> str:
        nonlocal calls
        calls += 1
        return "committed"

    with pytest.raises(LostResponseFault):
        injector.invoke(operation, point=plan.injection_point)
    assert calls == 1
    assert injector.invoke_with_retries(operation, point=plan.injection_point) == "committed"
    assert calls == 2
    assert injector.injected == 1
    assert injector.retries == 0


def test_duplicate_command_wrapper_invokes_same_operation_twice() -> None:
    plan = next(plan for plan in load_fault_plans() if plan.fault_id == "F6_duplicate_command")
    injector = FaultInjector(plan)
    calls = []
    result = injector.invoke(lambda: calls.append("side-effect") or len(calls), point=plan.injection_point)
    assert result == 2
    assert calls == ["side-effect", "side-effect"]
