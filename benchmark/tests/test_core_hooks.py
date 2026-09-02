from __future__ import annotations

import pytest

from core.backend.app.agents.models import AgentInvocation, PublicDecisionContext
from core.backend.app.agents.runtime import NPCAgentRuntime
from core.backend.app.ai.decision_service import DecisionService
from core.backend.app.ai.protocols import DailyActionDecision
from core.backend.app.persistence.memory_retriever import RetrievalPolicy


def test_retrieval_policy_keeps_owner_guard_non_configurable() -> None:
    policy = RetrievalPolicy(use_vector=False, graph_hops=0)
    assert policy.use_keyword is True
    assert policy.use_vector is False
    assert not hasattr(policy, "owner_guard")
    with pytest.raises(ValueError, match="graph_hops"):
        RetrievalPolicy(graph_hops=3)
    assert RetrievalPolicy(max_seed_candidates=1).max_seed_candidates == 1
    with pytest.raises(ValueError, match="max_seed_candidates"):
        RetrievalPolicy(max_seed_candidates=0)


@pytest.mark.anyio
async def test_public_decision_policy_never_receives_prompt_or_memory() -> None:
    observed: list[PublicDecisionContext] = []

    async def policy(context: PublicDecisionContext) -> DailyActionDecision:
        observed.append(context)
        return DailyActionDecision(action="wait")

    runtime = NPCAgentRuntime(DecisionService(None), decision_policy=policy)
    agent = runtime.create_agent("npc_001")
    result = await agent.daily_tick(
        AgentInvocation(
            run_id="run-1",
            npc_id="npc_001",
            event_type="daily_tick",
            prompt="PRIVATE SECRET",
            memory_cache=("PRIVATE MEMORY",),
            candidate_actor_ids=("npc_002",),
        )
    )

    assert result.decision.action == "wait"
    assert result.node_path == ("public_decision_policy", "finalize")
    assert observed[0].candidate_actor_ids == ("npc_002",)
    assert not hasattr(observed[0], "prompt")
    assert not hasattr(observed[0], "memory_cache")
