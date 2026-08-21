"""Offline contract tests for the NPC Agent building blocks.

These tests intentionally use only immutable in-memory snapshots.  They are
the first layer of the LangGraph migration tests: the graph tests exercise
the runtime, while this module keeps the privacy and tool contracts small and
easy to diagnose when a graph node changes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy

import pytest
from core.backend.app.agents.memory_tool import RetrieveOwnedMemoriesTool
from core.backend.app.agents.models import (
    AgentInvocation,
    MemoryToolContext,
    MemoryToolResult,
    invocation_state,
)
from core.backend.app.agents.runtime import NPCAgentRegistry, NPCAgentRuntime
from core.backend.app.agents.trace import AgentTrace, InMemoryAgentTraceSink
from core.backend.app.ai.decision_service import DecisionService
from core.backend.app.ai.models import TextGenerationResult
from core.backend.app.ai.protocols import ChatDecision, MemoryQuery, SpeechGeneration
from pydantic import ValidationError


def _context(owner: str = "npc_001") -> MemoryToolContext:
    return MemoryToolContext(
        owner_npc_id=owner,
        run_id="run_test",
        conversation_id="conv_test",
        memories={
            "m_own_actor": {
                "memoryId": "m_own_actor",
                "ownerNpcId": owner,
                "content": "玩家曾在书店帮助我整理旧书。",
                "actorIds": ["player_001"],
                "topicIds": ["topic_bookstore_survival"],
                "goalIds": ["goal_001_public"],
                "importance": 3,
            },
            "m_own_other": {
                "memoryId": "m_own_other",
                "ownerNpcId": owner,
                "content": "沈星遥愿意为文社画宣传图。",
                "actorIds": ["npc_002"],
                "topicIds": ["topic_literary_society"],
                "goalIds": ["goal_001_public"],
                "importance": 4,
            },
            "m_foreign_similar": {
                "memoryId": "m_foreign_similar",
                "ownerNpcId": "npc_002" if owner == "npc_001" else "npc_001",
                "content": "玩家曾在书店帮助我整理旧书。",
                "actorIds": ["player_001"],
                "topicIds": ["topic_bookstore_survival"],
                "goalIds": ["goal_001_public"],
                "importance": 5,
            },
        },
        topics={
            "topic_bookstore_survival": {
                "name": "书店存续",
                "aliases": ["保住书店"],
            },
            "topic_literary_society": {
                "name": "青槐文社",
                "aliases": ["文社"],
            },
        },
    )


class FakeTextModel:
    """Offline structured-output model with protocol-aware scripted replies."""

    def __init__(self, replies: list[dict[str, object]]) -> None:
        self.replies = list(replies)
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        if not self.replies:
            raise AssertionError("unexpected model call")
        return TextGenerationResult(
            text=json.dumps(self.replies.pop(0), ensure_ascii=False),
            provider="fake",
            model="offline",
        )


class FakeMemoryTool(RetrieveOwnedMemoriesTool):
    """Tool double used to prove graph call count and safe failure paths."""

    def __init__(self, result: MemoryToolResult | Exception) -> None:
        super().__init__()
        self.result = result
        self.calls = 0

    async def ainvoke(
        self,
        query: MemoryQuery | Mapping[str, object],
        *,
        context: MemoryToolContext,
        agent_npc_id: str,
    ) -> MemoryToolResult:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _invocation(
    *,
    npc_id: str = "npc_001",
    event_type: str = "chat_message_received",
    prompt: str = "{}",
    memory_tool_context: MemoryToolContext | None = None,
    prompt_builder=None,
) -> AgentInvocation:
    return AgentInvocation(
        run_id="run_test",
        npc_id=npc_id,
        event_type=event_type,  # type: ignore[arg-type]
        prompt=prompt,
        conversation_id="conv_test",
        trigger_message_id="msg_test",
        candidate_actor_ids=("npc_002",),
        visible_messages=({"messageId": "msg_test", "text": "公开内容"},),
        memory_cache=(),
        memory_context=memory_tool_context,
        prompt_builder=prompt_builder,
    )


def test_memory_query_exposes_only_model_search_fields() -> None:
    query = MemoryQuery.model_validate(
        {
            "queryText": "书店",
            "actorIds": ["player_001"],
            "limit": 1,
        }
    )
    assert query.limit == 1
    assert "ownerNpcId" not in MemoryQuery.model_json_schema()["properties"]
    with pytest.raises(ValidationError):
        MemoryQuery.model_validate(
            {
                "queryText": "书店",
                "ownerNpcId": "npc_002",
            }
        )
    with pytest.raises(ValidationError):
        MemoryQuery.model_validate({"queryText": "书店", "limit": 0})
    with pytest.raises(ValidationError):
        MemoryQuery.model_validate({"queryText": "书店", "limit": 9})


def test_memory_tool_injects_owner_and_never_returns_similar_foreign_memory() -> None:
    context = _context()
    before = deepcopy(context.memories)
    tool = RetrieveOwnedMemoriesTool(bound_owner_npc_id="npc_001")

    result = tool.invoke(
        MemoryQuery.model_validate(
            {"queryText": "书店", "actorIds": ["player_001"], "limit": 8}
        ),
        context=context,
        agent_npc_id="npc_001",
    )

    assert "m_own_actor" in result.memory_ids
    assert "m_foreign_similar" not in result.memory_ids
    assert context.memories == before


def test_memory_tool_rejects_owner_mismatch_before_search() -> None:
    tool = RetrieveOwnedMemoriesTool(bound_owner_npc_id="npc_001")
    with pytest.raises(ValueError, match="bound Agent"):
        tool.invoke(
            {"queryText": "书店", "actorIds": ["player_001"]},
            context=_context("npc_001"),
            agent_npc_id="npc_002",
        )
    with pytest.raises(ValueError, match="owner"):
        tool.invoke(
            {"queryText": "书店", "actorIds": ["player_001"]},
            context=_context("npc_002"),
            agent_npc_id="npc_001",
        )


def test_memory_tool_uses_topic_alias_and_limit() -> None:
    context = _context()
    result = RetrieveOwnedMemoriesTool().invoke(
        {"topicHints": ["文社"], "limit": 1},
        context=context,
        agent_npc_id="npc_001",
    )
    assert result.memory_ids == ("m_own_other",)


def test_trace_sink_is_internal_snapshot_only() -> None:
    sink = InMemoryAgentTraceSink()
    trace = AgentTrace(
        trace_id="trace_test",
        run_id="run_test",
        npc_id="npc_001",
        event_type="chat_message_received",
        conversation_id="conv_test",
        node_path=("route_event", "chat_decision", "finalize"),
        tool_used=False,
        tool_result_count=0,
        final_action="wait",
        duration_ms=1.0,
    )
    sink.record(trace)
    assert tuple(sink) == (trace,)
    # The trace object deliberately contains only diagnostics, not prompts,
    # query text, memory content, secrets, or the public event payload.
    assert not hasattr(trace, "prompt")
    assert not hasattr(trace, "memory_content")
    assert not hasattr(trace, "graph_state")


def test_invocation_state_copies_visible_messages_and_memory_context() -> None:
    visible = [{"messageId": "msg_1", "text": "只给林慧兰看的消息"}]
    context = _context()
    invocation = AgentInvocation(
        run_id="run_test",
        npc_id="npc_001",
        event_type="chat_message_received",
        prompt="prompt",
        conversation_id="conv_test",
        trigger_message_id="msg_1",
        visible_messages=tuple(visible),
        memory_cache=("m_own_actor",),
        memory_context=context,
    )
    state = invocation_state(invocation, trace_id="trace_test", memory_tool=object())

    visible[0]["text"] = "调用方之后修改的内容"
    context.memories["m_own_actor"]["content"] = "调用方之后修改的记忆"  # type: ignore[index]
    assert state["visible_messages"][0]["text"] == "只给林慧兰看的消息"
    assert state["memory_tool_context"].memories["m_own_actor"]["content"] == (
        "玩家曾在书店帮助我整理旧书。"
    )
    assert state["npc_id"] == "npc_001"
    assert state["recall_used"] is False


@pytest.mark.anyio
async def test_agent_trace_never_enters_public_run_or_event_payload(registry) -> None:
    # The engine may execute daily_tick traces during this step, but its
    # public projection remains the existing player-facing contract.
    from core.backend.app.orchestration.run_service import RunService

    service = RunService(registry, text_model=None, seed=19)
    created = await service.create_run()
    await service.world_step(created["runId"], 1080)
    snapshot = await service.get_run(created["runId"])
    events = await service.get_events(created["runId"])
    public_text = str({"snapshot": snapshot, "events": events})
    assert "traceId" not in public_text
    assert "nodePath" not in public_text
    assert "graphState" not in public_text
    assert "memoryQuery" not in public_text


def test_five_logical_agents_share_runtime_but_have_private_tools() -> None:
    model = FakeTextModel([])
    decisions = DecisionService(model)
    runtime = NPCAgentRuntime(decisions)
    agents = NPCAgentRegistry(runtime, [f"npc_{index:03d}" for index in range(1, 6)])

    assert len(agents) == 5
    assert len({id(agent.runtime) for agent in agents}) == 1
    assert len({id(agent.memory_tool) for agent in agents}) == 5
    assert all(agent.runtime.decisions is decisions for agent in agents)
    assert all(
        agent.memory_tool.bound_owner_npc_id == agent.actor_id for agent in agents
    )
    assert runtime.graph is runtime.compiled_graph


@pytest.mark.anyio
async def test_three_agent_entrypoints_are_routed_and_traced() -> None:
    model = FakeTextModel(
        [
            {"action": "wait"},
            {"decision": "refuse"},
            {"result": "decided", "action": "wait"},
        ]
    )
    trace_sink = InMemoryAgentTraceSink()
    runtime = NPCAgentRuntime(DecisionService(model), trace_sink=trace_sink)
    agent = runtime.create_agent("npc_001")

    daily = await agent.daily_tick(_invocation(event_type="daily_tick"))
    invitation = await agent.invitation_received(
        _invocation(event_type="invitation_received")
    )
    chat = await agent.chat_message_received(_invocation())

    assert daily.decision.action == "wait"
    assert invitation.decision.decision == "refuse"
    assert chat.decision.action == "wait"
    assert "position" not in daily.decision.model_dump()
    assert "conversationId" not in daily.decision.model_dump()
    traces = trace_sink.snapshot()
    assert [trace.event_type for trace in traces] == [
        "daily_tick",
        "invitation_received",
        "chat_message_received",
    ]
    assert traces[0].node_path == ("route_event", "daily_decision", "finalize")
    assert traces[1].node_path == (
        "route_event",
        "invitation_decision",
        "finalize",
    )
    assert traces[2].node_path == ("route_event", "chat_decision", "finalize")
    assert all(not trace.tool_used for trace in traces)


@pytest.mark.anyio
async def test_invitation_agent_only_receives_public_request_context() -> None:
    model = FakeTextModel([{"decision": "accept"}])
    runtime = NPCAgentRuntime(DecisionService(model))
    agent = runtime.create_agent("npc_002")
    invocation = _invocation(
        npc_id="npc_002",
        event_type="invitation_received",
        prompt=json.dumps(
            {
                "initiatorActorId": "npc_001",
                "visibleRequest": True,
            },
            ensure_ascii=False,
        ),
    )

    result = await agent.invitation_received(invocation)

    assert result.decision.decision == "accept"
    sent_prompt = model.requests[0].messages[0].content
    assert "_goalId" not in sent_prompt
    assert "_intent" not in sent_prompt
    assert "privateSecret" not in sent_prompt
    assert "initiatorActorId" in sent_prompt


@pytest.mark.anyio
async def test_runservice_invitation_hides_initiator_private_goal_and_intent(
    registry,
) -> None:
    from core.backend.app.orchestration.run_service import RunService

    service = RunService(registry, text_model=None)
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    model = FakeTextModel([{"decision": "refuse"}])
    service.decisions.model = model

    async with run.lock:
        await service._request_invitation_locked(
            run,
            "npc_001",
            "npc_002",
            private_goal_id="goal_private_marker",
            private_intent="intent_private_marker",
        )

    assert len(model.requests) == 1
    sent_prompt = model.requests[0].messages[0].content
    assert "goal_private_marker" not in sent_prompt
    assert "intent_private_marker" not in sent_prompt
    assert '"initiatorActorId": "npc_001"' in sent_prompt


@pytest.mark.anyio
async def test_agent_daily_decision_cannot_mutate_world_position(registry) -> None:
    from core.backend.app.orchestration.run_service import RunService

    model = FakeTextModel(
        [
            {
                "action": "seek_chat",
                "goalId": "goal_001_public",
                "targetActorId": "npc_002",
                "intent": "讨论文社",
            }
        ]
    )
    service = RunService(registry, text_model=None)
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    service.decisions.model = model
    before = deepcopy(run.positions)

    result = await service.agents["npc_001"].daily_tick(
        _invocation(npc_id="npc_001", event_type="daily_tick")
    )

    assert result.decision.action == "seek_chat"
    assert run.positions == before


@pytest.mark.anyio
async def test_chat_recall_is_once_and_second_request_becomes_wait() -> None:
    model = FakeTextModel(
        [
            {
                "result": "need_memory",
                "memoryQuery": {"queryText": "书店"},
            },
            {
                "result": "need_memory",
                "memoryQuery": {"queryText": "还是书店"},
            },
        ]
    )
    tool = FakeMemoryTool(MemoryToolResult(("m_own_actor",)))
    runtime = NPCAgentRuntime(
        DecisionService(model),
        memory_tool_factory=lambda _actor_id: tool,
    )
    agent = runtime.create_agent("npc_001")
    result = await agent.chat_message_received(
        _invocation(memory_tool_context=_context())
    )

    assert result.decision.action == "wait"
    assert tool.calls == 1
    assert result.recalled_memory_ids == ("m_own_actor",)
    assert result.tool_used is True
    assert result.tool_result_count == 1
    assert result.failure_code == "recall_limit"
    trace = runtime.trace_sink.snapshot()[-1]
    assert trace.node_path == (
        "route_event",
        "chat_decision",
        "retrieve_owned_memories",
        "chat_after_recall",
        "safe_wait",
        "finalize",
    )
    assert trace.tool_result_count == 1


@pytest.mark.anyio
async def test_same_trigger_message_cannot_recall_again_in_later_invocation() -> None:
    model = FakeTextModel(
        [
            {"result": "need_memory", "memoryQuery": {"queryText": "书店"}},
            {"result": "decided", "action": "wait"},
            {"result": "need_memory", "memoryQuery": {"queryText": "书店"}},
        ]
    )
    tool = FakeMemoryTool(MemoryToolResult(("m_own_actor",)))
    runtime = NPCAgentRuntime(
        DecisionService(model),
        memory_tool_factory=lambda _actor_id: tool,
    )
    agent = runtime.create_agent("npc_001")
    invocation = _invocation(memory_tool_context=_context())

    first = await agent.chat_message_received(invocation)
    second = await agent.chat_message_received(invocation)

    assert first.recalled_memory_ids == ("m_own_actor",)
    assert second.decision.action == "wait"
    assert second.failure_code == "recall_limit"
    assert tool.calls == 1


@pytest.mark.anyio
async def test_runservice_agent_state_snapshot_contains_only_owner_memories(
    registry,
) -> None:
    from core.backend.app.orchestration.run_service import RunService

    service = RunService(registry, text_model=None)
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    context = service._memory_tool_context(run, "npc_001", "conv_test")

    assert context.memories
    assert all(
        memory["ownerNpcId"] == "npc_001"
        for memory in context.memories.values()
    )
    assert any(
        memory.get("ownerNpcId") == "npc_002"
        for memory in run.memories.values()
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool_result", "failure"),
    [
        (MemoryToolResult(()), "tool_empty"),
        (RuntimeError("offline tool failure"), "tool_error"),
    ],
)
async def test_empty_or_failed_recall_is_one_call_and_safe_wait(
    tool_result: MemoryToolResult | Exception, failure: str
) -> None:
    model = FakeTextModel(
        [
            {
                "result": "need_memory",
                "memoryQuery": {"queryText": "书店"},
            }
        ]
    )
    tool = FakeMemoryTool(tool_result)
    runtime = NPCAgentRuntime(
        DecisionService(model),
        memory_tool_factory=lambda _actor_id: tool,
    )
    agent = runtime.create_agent("npc_001")

    result = await agent.chat_message_received(
        _invocation(memory_tool_context=_context())
    )

    assert result.decision.action == "wait"
    assert len(model.requests) == 1
    assert tool.calls == 1
    assert result.failure_code == failure
    trace = runtime.trace_sink.snapshot()[-1]
    assert trace.tool_used is True
    assert trace.tool_result_count == 0
    assert trace.node_path[-2:] == ("safe_wait", "finalize")


@pytest.mark.anyio
async def test_agent_binding_rejects_other_npc_invocation() -> None:
    runtime = NPCAgentRuntime(DecisionService(FakeTextModel([])))
    agent = runtime.create_agent("npc_001")
    with pytest.raises(ValueError, match="binding"):
        await agent.chat_message_received(
            _invocation(npc_id="npc_002")
        )


@pytest.mark.anyio
async def test_conversation_scheduler_calls_speech_only_on_winning_agent(
    registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.backend.app.orchestration.run_service import RunService

    service = RunService(registry, text_model=None)
    created = await service.create_run()
    opened = await service.create_conversation(
        created["runId"], ["npc_001", "npc_002"]
    )
    conversation_id = opened["conversation"]["conversationId"]
    run = await service.get_run_entity(created["runId"])
    conversation = run.conversations[conversation_id]
    decisions = {
        "npc_001": ChatDecision.model_validate(
            {
                "result": "decided",
                "action": "speak",
                "responseDesire": 3,
                "intent": "先谈书店",
            }
        ),
        "npc_002": ChatDecision.model_validate(
            {
                "result": "decided",
                "action": "speak",
                "responseDesire": 1,
                "intent": "听听看",
            }
        ),
    }
    speech_calls: list[str] = []

    async def fake_decision(_run, _conversation, npc_id, _trigger, _message_id=None):
        return decisions[npc_id]

    async def speech_one(_prompt: str) -> SpeechGeneration:
        speech_calls.append("npc_001")
        return SpeechGeneration(text="林慧兰开口了。")

    async def speech_two(_prompt: str) -> SpeechGeneration:
        speech_calls.append("npc_002")
        return SpeechGeneration(text="沈星遥开口了。")

    monkeypatch.setattr(service, "_run_one_chat_decision_locked", fake_decision)
    monkeypatch.setattr(service.agents["npc_001"], "generate_speech", speech_one)
    monkeypatch.setattr(service.agents["npc_002"], "generate_speech", speech_two)
    # Prevent the speech generated in this test from starting a second round;
    # the outer call still runs the real scheduler and winner selection.
    real_pipeline = service._run_chat_pipeline_locked

    async def no_recursive_pipeline(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_run_chat_pipeline_locked", no_recursive_pipeline)
    async with run.lock:
        await real_pipeline(run, conversation, None, chain_left=1)

    assert speech_calls == ["npc_001"]
