from __future__ import annotations

import asyncio

from benchmark.business import (
    BusinessTaskRunner,
    MyopicRulePolicy,
    NoopPolicy,
    RandomLegalPolicy,
    aggregate_paired_results,
    load_tasks,
    score_task_state,
)


def test_catalog_has_frozen_shape() -> None:
    tasks = load_tasks()
    assert len(tasks) == 12
    assert {task.category for task in tasks} == {
        "permit_commitment",
        "cross_day_commitment",
        "information_asymmetry",
        "conflict_repair",
    }
    counts = {category: sum(task.category == category for task in tasks) for category in {task.category for task in tasks}}
    assert counts == {
        "permit_commitment": 4,
        "cross_day_commitment": 3,
        "information_asymmetry": 3,
        "conflict_repair": 2,
    }
    for task in tasks:
        assert set(task.raw["routes"]) == {"autonomous", "player_assisted"}
        assert task.full_plan
        assert set(task.full_plan) <= {action["id"] for action in task.actions}


def test_policies_are_frozen_and_public_for_b2() -> None:
    state = {
        "legalActions": [
            {"id": "goal_action", "kind": "commit", "goalId": "g1"},
            {"id": "wait", "kind": "wait"},
        ],
        "publicGoals": ["g1"],
        "privateFacts": ["do-not-read"],
    }
    assert NoopPolicy().choose(state).action["id"] == "wait"
    left = RandomLegalPolicy(seed=7).choose(state).action
    right = RandomLegalPolicy(seed=7).choose(state).action
    assert left == right
    decision = MyopicRulePolicy().choose(state)
    assert decision.action["id"] == "goal_action"
    assert decision.used_public_state is True


def test_policy_view_hides_plan_and_action_effects() -> None:
    from benchmark.business.policies import public_state_view

    view = public_state_view(
        {
            "fullPlan": ["secret_next_action"],
            "pendingActionIds": ["secret_next_action"],
            "privateFacts": ["CANARY"],
            "legalActions": [
                {
                    "id": "public_action",
                    "kind": "commit",
                    "goalId": "g1",
                    "effects": {"chapter": {"answer": "adopted"}},
                }
            ],
        }
    )
    assert "fullPlan" not in view
    assert "pendingActionIds" not in view
    assert "privateFacts" not in view
    assert "effects" not in view["legalActions"][0]


def test_a0_and_b0_have_auditable_outcomes() -> None:
    runner = BusinessTaskRunner()
    task_id = "task_literary_society_permission"
    full = runner.run_task_sync(task_id, "A0_full", seed=11)
    noop = runner.run_task_sync(task_id, "B0_noop", seed=11)
    assert full.status == "completed"
    assert full.gameplay_success is True
    assert full.score.failure_reasons == ()
    assert noop.status == "failed"
    assert noop.gameplay_success is False
    assert noop.score.failure_type in {"authorization_missing", "commitment_missing", "chapter_incomplete"}
    assert full.attempt is not None
    assert full.telemetry is not None


def test_every_task_full_policy_completes_both_routes() -> None:
    runner = BusinessTaskRunner()
    for task in load_tasks():
        for route in ("autonomous", "player_assisted"):
            result = runner.run_task_sync(task.task_id, "A0_full", seed=0, route=route)
            assert result.gameplay_success, (task.task_id, route, result.score.failure_reasons)


def test_paired_aggregate_keeps_infra_invalid_and_reports_delta() -> None:
    runner = BusinessTaskRunner()
    results = runner.run_matrix_sync(
        task_ids=["task_literary_society_permission"],
        conditions=("A0_full", "B1_random_legal", "B2_myopic_rule"),
        seeds=(0, 1),
        routes=("autonomous",),
    )
    report = aggregate_paired_results(results, bootstrap_resamples=200)
    assert report["sampleCount"] == 6
    assert report["pairCount"] == 2
    assert set(report["conditions"]) == {"A0_full", "B1_random_legal", "B2_myopic_rule"}
    effect = report["pairedEffects"]["fullVsRandom"]
    assert effect["pairedCount"] == 2
    assert effect["confidenceInterval95"]["low"] is not None
    assert effect["confidenceInterval95"]["high"] is not None


def test_async_decider_adapter_and_trace_sink() -> None:
    seen: list[dict[str, object]] = []

    async def decider(state: dict[str, object]) -> dict[str, object]:
        actions = state["legalActions"]
        assert isinstance(actions, list)
        # The live decider sees only public action affordances.  The test
        # chooses the first non-wait action, while A0's plan remains private
        # to the adapter in production.
        action = next((item for item in actions if item.get("kind") != "wait"), {"id": "wait"})
        return {"id": action["id"]}

    runner = BusinessTaskRunner(trace_sink=seen.append)
    result = asyncio.run(
        runner.run_task(
            "task_chen_medication_repair",
            "A0_full",
            seed=3,
            decider=decider,
        )
    )
    assert result.gameplay_success is True
    assert result.telemetry is not None
    assert seen


def test_scorer_uses_state_contract_not_text() -> None:
    task = load_tasks()[0].raw
    state = {
        "worldDay": 7,
        "authorization": {"bookstore": "granted"},
        "commitments": {"npc_002": "committed", "npc_003": "committed", "npc_004": "committed"},
        "chapter": {"agenda_001_literary_society": "adopted"},
        "generatedText": "the model claimed success",
    }
    score = score_task_state(task, state)
    assert score.success is True
    state["chapter"]["agenda_001_literary_society"] = "pending"
    assert score_task_state(task, state).success is False
