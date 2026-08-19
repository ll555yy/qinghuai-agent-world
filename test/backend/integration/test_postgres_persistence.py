from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from core.backend.app.ai.ark_client import ArkClient
from core.backend.app.ai.models import TextGenerationResult
from core.backend.app.ai.protocols import MemoryQuery
from core.backend.app.db.bootstrap import sync_scenario
from core.backend.app.db.models import (
    ChapterResolution,
    Goal,
    Memory,
    MemoryActorLink,
    MemoryEdge,
    MemoryGoalLink,
    MemoryTopicLink,
    Message,
)
from core.backend.app.main import create_app
from core.backend.app.orchestration.run_service import RunService
from core.backend.app.persistence.memory_retriever import DatabaseMemoryRetriever
from core.backend.app.persistence.run_repository import RepositoryConflictError
from core.backend.app.persistence.sqlalchemy_repository import SQLAlchemyRunRepository
from core.backend.app.scenario.loader import ScenarioLoader
from core.backend.app.settings import Settings
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError


class _FixedEmbedding:
    dimensions = 384
    model_name = "test-fixed-384"

    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, _text: str) -> list[float]:
        self.calls += 1
        return [1.0] * self.dimensions


def test_postgres_fastapi_startup_and_restart_recovery() -> None:
    database_url = os.getenv("QINGHUAI_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set QINGHUAI_TEST_DATABASE_URL to run PostgreSQL integration tests")
    scenario_dir = Path(__file__).resolve().parents[3] / "core" / "scenario"
    settings = Settings(
        scenario_dir=scenario_dir,
        persistence_backend="postgres",
        database_url=database_url,
    )
    fake_generate = AsyncMock(
        return_value=TextGenerationResult(text="{}", provider="test", model="test")
    )
    with patch.object(ArkClient, "generate", fake_generate):
        with TestClient(create_app(settings)) as first_client:
            health = first_client.get("/api/health")
            assert health.status_code == 200
            assert health.json()["persistence"] == "postgres"
            assert health.json()["storageHealthy"] is True
            created = first_client.post("/api/runs", json={})
            assert created.status_code == 201
            run_id = created.json()["runId"]
        with TestClient(create_app(settings)) as restarted_client:
            recovered = restarted_client.get(f"/api/runs/{run_id}")
            assert recovered.status_code == 200
            assert recovered.json()["runId"] == run_id


@pytest.mark.anyio
async def test_postgres_repository_recovers_run_and_durable_events() -> None:
    database_url = os.getenv("QINGHUAI_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set QINGHUAI_TEST_DATABASE_URL to run PostgreSQL integration tests")
    scenario_dir = Path(__file__).resolve().parents[3] / "core" / "scenario"
    registry = ScenarioLoader(scenario_dir).load()
    first_repository = SQLAlchemyRunRepository(
        database_url,
        chapter_id=registry.chapter_id,
    )
    await sync_scenario(first_repository.session_factory, registry)
    first_service = RunService(registry, repository=first_repository, text_model=None)
    created = await first_service.create_run(seed=1907)
    run_id = str(created["runId"])
    await first_service.player_invite(
        run_id,
        "npc_002",
        command_id="postgres-player-invitation",
    )
    conversation_result = await first_service.create_conversation(
        run_id,
        [registry.player_actor_id, "npc_001"],
        command_id="postgres-create-conversation",
    )
    conversation_id = str(conversation_result["conversation"]["conversationId"])
    await first_service.player_message(
        run_id,
        conversation_id,
        "我想听听你对保住书店的看法。",
        command_id="postgres-player-message",
    )
    await first_service.remove_participant(
        run_id,
        conversation_id,
        "npc_001",
        command_id="postgres-npc-leave",
    )
    before_restart = await first_service.get_run(run_id)
    durable_before = await first_service.get_events(run_id, 0)
    await first_repository.close()

    second_repository = SQLAlchemyRunRepository(
        database_url,
        chapter_id=registry.chapter_id,
    )
    second_service = RunService(registry, repository=second_repository, text_model=None)
    after_restart = await second_service.get_run(run_id)
    messages = await second_service.get_messages(run_id, conversation_id)
    durable_after = await second_service.get_events(run_id, 0)

    assert after_restart["worldTime"] == before_restart["worldTime"]
    assert after_restart["stateVersion"] == before_restart["stateVersion"]
    assert messages["messages"][0]["text"] == "我想听听你对保住书店的看法。"
    assert durable_after == durable_before
    await second_service.create_conversation(
        run_id,
        [registry.player_actor_id, "npc_001"],
        command_id="postgres-create-conversation",
    )
    assert (await second_service.get_run(run_id))["eventSeq"] == after_restart["eventSeq"]
    current_revision = await second_repository.revision(run_id)
    assert current_revision is not None and current_revision > 0
    recovered_run = await second_service.get_run_entity(run_id)
    with pytest.raises(RepositoryConflictError):
        await second_repository.save(recovered_run, expected_revision=0)
    continued = await second_service.advance_time(
        run_id,
        1,
        command_id="postgres-continue-after-restart",
    )
    assert continued["run"]["eventSeq"] > after_restart["eventSeq"]

    async with second_repository.session_factory() as session:
        message_count = await session.scalar(
            select(func.count()).select_from(Message).where(Message.run_id == run_id)
        )
        goal_count = await session.scalar(
            select(func.count()).select_from(Goal).where(Goal.run_id == run_id)
        )
        own_memories = list(
            (
                await session.execute(
                    select(Memory.memory_id).where(
                        Memory.run_id == run_id,
                        Memory.owner_npc_id == "npc_001",
                    )
                )
            ).scalars()
        )
        other_memory = await session.scalar(
            select(Memory.memory_id).where(
                Memory.run_id == run_id,
                Memory.owner_npc_id == "npc_002",
            )
        )
        assert message_count and message_count >= 1
        assert goal_count == len(registry.goals)
        assert len(own_memories) >= 2
        assert other_memory is not None
        seed_memory, neighbor_memory = own_memories[:2]
        await session.execute(
            update(Memory)
            .where(
                Memory.run_id == run_id,
                Memory.memory_id.in_([seed_memory, neighbor_memory, other_memory]),
            )
            .values(
                embedding=[1.0] * 384,
                embedding_model="test-fixed-384",
                embedding_dimensions=384,
            )
        )
        await session.execute(
            insert(MemoryActorLink)
            .values(
                run_id=run_id,
                memory_id=seed_memory,
                actor_id=registry.player_actor_id,
                link_role="mentioned",
            )
            .on_conflict_do_nothing()
        )
        await session.execute(
            insert(MemoryGoalLink)
            .values(
                run_id=run_id,
                memory_id=seed_memory,
                goal_id=next(iter(registry.goals)),
                role="evidence",
            )
            .on_conflict_do_nothing()
        )
        await session.execute(
            insert(MemoryTopicLink)
            .values(
                run_id=run_id,
                memory_id=seed_memory,
                topic_id=next(iter(registry.topics)),
            )
            .on_conflict_do_nothing()
        )
        await session.execute(
            insert(MemoryEdge)
            .values(
                [
                    {
                        "run_id": run_id,
                        "from_memory_id": seed_memory,
                        "to_memory_id": neighbor_memory,
                        "edge_type": "SUPPORTS",
                        "created_world_day": 1,
                        "created_world_minute": 540,
                    },
                    {
                        "run_id": run_id,
                        "from_memory_id": seed_memory,
                        "to_memory_id": other_memory,
                        "edge_type": "CONTRADICTS",
                        "created_world_day": 1,
                        "created_world_minute": 540,
                    },
                ]
            )
            .on_conflict_do_nothing()
        )
        await session.commit()

    embedding = _FixedEmbedding()
    retriever = DatabaseMemoryRetriever(
        second_repository.session_factory,
        embedding_port=embedding,
    )
    recalled = await retriever.search(
        run_id=run_id,
        owner_npc_id="npc_001",
        query=MemoryQuery(
            queryText="书店方案",
            actorIds=[registry.player_actor_id],
            goalIds=[next(iter(registry.goals))],
            topicHints=[next(iter(registry.topics))],
            limit=8,
        ),
    )
    assert embedding.calls == 1
    assert seed_memory in recalled.memory_ids
    assert neighbor_memory in recalled.memory_ids
    assert other_memory not in recalled.memory_ids
    async with second_repository.session_factory() as session:
        recalled_owners = set(
            (
                await session.execute(
                    select(Memory.owner_npc_id).where(
                        Memory.run_id == run_id,
                        Memory.memory_id.in_(recalled.memory_ids),
                    )
                )
            ).scalars()
        )
    assert recalled_owners == {"npc_001"}
    await second_repository.close()


@pytest.mark.anyio
async def test_postgres_failed_transition_rolls_back_all_normalized_state() -> None:
    database_url = os.getenv("QINGHUAI_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set QINGHUAI_TEST_DATABASE_URL to run PostgreSQL integration tests")
    scenario_dir = Path(__file__).resolve().parents[3] / "core" / "scenario"
    registry = ScenarioLoader(scenario_dir).load()
    repository = SQLAlchemyRunRepository(database_url, chapter_id=registry.chapter_id)
    await sync_scenario(repository.session_factory, registry)
    service = RunService(registry, repository=repository, text_model=None)
    created = await service.create_run(seed=701)
    run_id = str(created["runId"])
    run = await service.get_run_entity(run_id)
    goal_id = next(iter(run.goals))
    relationship_key = next(iter(run.relationships))
    original_goal_status = run.goals[goal_id]["status"]
    original_trust = run.relationships[relationship_key]["trust"]
    async with run.lock:
        run.goals[goal_id]["status"] = "completed"
        run.relationships[relationship_key]["trust"] = 99
        run.chapter_actor_stances["npc_001"] = "support"
        with pytest.raises(IntegrityError):
            await repository.save(run)
    await repository.close()

    recovered_repository = SQLAlchemyRunRepository(
        database_url,
        chapter_id=registry.chapter_id,
    )
    recovered_service = RunService(
        registry,
        repository=recovered_repository,
        text_model=None,
    )
    recovered = await recovered_service.get_run_entity(run_id)
    assert recovered.goals[goal_id]["status"] == original_goal_status
    assert recovered.relationships[relationship_key]["trust"] == original_trust
    assert recovered.chapter_actor_stances["npc_001"] == "unknown"
    await recovered_repository.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("authorization", "stances", "expected"),
    [
        ("approved", ["support"] * 5, "consensus_submitted"),
        (
            "conditional",
            ["conditional", "conditional", "unknown", "unknown", "conditional"],
            "compromise_submitted",
        ),
        ("none", ["unknown"] * 5, "no_submission"),
    ],
)
async def test_postgres_persists_all_day7_resolution_branches(
    authorization: str,
    stances: list[str],
    expected: str,
) -> None:
    database_url = os.getenv("QINGHUAI_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set QINGHUAI_TEST_DATABASE_URL to run PostgreSQL integration tests")
    scenario_dir = Path(__file__).resolve().parents[3] / "core" / "scenario"
    registry = ScenarioLoader(scenario_dir).load()
    repository = SQLAlchemyRunRepository(database_url, chapter_id=registry.chapter_id)
    await sync_scenario(repository.session_factory, registry)
    service = RunService(registry, repository=repository, text_model=None)
    created = await service.create_run(
        agenda_id="agenda_001_literary_society",
        seed=900 + len(expected),
    )
    run_id = str(created["runId"])
    run = await service.get_run_entity(run_id)
    deadline = next(
        event
        for event in registry.events
        if event.event_id == "event_day7_proposal_deadline"
    )
    async with run.lock:
        run.zhou_authorization = authorization
        for npc, stance in zip(registry.npcs, stances, strict=True):
            run.chapter_actor_stances[npc.actor_id] = stance
            run.chapter_agenda_stances[
                ("agenda_001_literary_society", npc.actor_id)
            ] = stance
        await service._finish_chapter_locked(run, deadline)
        await repository.save(run)
    await repository.close()

    recovered_repository = SQLAlchemyRunRepository(
        database_url,
        chapter_id=registry.chapter_id,
    )
    recovered_service = RunService(
        registry,
        repository=recovered_repository,
        text_model=None,
    )
    recovered = await recovered_service.get_run_entity(run_id)
    assert recovered.chapter_resolution is not None
    assert recovered.chapter_resolution["branch"] == expected
    async with recovered_repository.session_factory() as session:
        stored_branch = await session.scalar(
            select(ChapterResolution.branch).where(ChapterResolution.run_id == run_id)
        )
    assert stored_branch == expected
    await recovered_repository.close()
