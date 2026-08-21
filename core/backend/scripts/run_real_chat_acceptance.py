"""Run one bounded, privacy-preserving real NPC chat acceptance flow.

The command is inert unless ``--live`` is supplied. It drives only public
RunService/WorldEngine operations and writes a metrics-only JSON report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_ROOT / ".env", override=False)
sys.path.insert(0, str(_ROOT))

from core.backend.app.ai.ark_client import ArkClient  # noqa: E402
from core.backend.app.ai.ark_embedding import (  # noqa: E402
    DEFAULT_ARK_EMBEDDING_BASE_URL,
    ArkEmbeddingClient,
    ArkEmbeddingSettings,
)
from core.backend.app.db.bootstrap import sync_scenario  # noqa: E402
from core.backend.app.db.models import ChapterRun, Memory  # noqa: E402
from core.backend.app.orchestration.run_service import RunService  # noqa: E402
from core.backend.app.orchestration.world_engine import WorldEngine  # noqa: E402
from core.backend.app.persistence.embedding_indexer import (  # noqa: E402
    MemoryEmbeddingIndexer,
)
from core.backend.app.persistence.indexing_repository import (  # noqa: E402
    IndexingRunRepository,
)
from core.backend.app.persistence.memory_retriever import (  # noqa: E402
    DatabaseMemoryRetriever,
)
from core.backend.app.persistence.sqlalchemy_repository import (  # noqa: E402
    SQLAlchemyRunRepository,
)
from core.backend.app.scenario.loader import ScenarioLoader  # noqa: E402
from sqlalchemy import delete, func, select  # noqa: E402


class _BoundedTextModel:
    """Count logical calls while delegating provider retries to ArkClient."""

    configured = True

    def __init__(self, delegate: ArkClient, maximum: int) -> None:
        self.delegate = delegate
        self.maximum = maximum
        self.calls = 0

    async def generate(self, request: Any) -> Any:
        if self.calls >= self.maximum:
            raise RuntimeError("real chat model-call budget exhausted")
        self.calls += 1
        return await self.delegate.generate(request)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--seed", type=int, default=20260820)
    parser.add_argument("--max-model-calls", type=int, default=120)
    parser.add_argument("--step-timeout-seconds", type=float, default=300.0)
    parser.add_argument("--run-timeout-seconds", type=float, default=1200.0)
    parser.add_argument("--keep-run", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("simulation_reports/real_chat_acceptance.json"),
    )
    return parser.parse_args()


async def _database_counts(repository: SQLAlchemyRunRepository, run_id: str) -> dict[str, int]:
    async with repository.session_factory() as session:
        total = await session.scalar(
            select(func.count()).select_from(Memory).where(Memory.run_id == run_id)
        )
        embedded = await session.scalar(
            select(func.count())
            .select_from(Memory)
            .where(Memory.run_id == run_id, Memory.embedding.is_not(None))
        )
        conversation = await session.scalar(
            select(func.count())
            .select_from(Memory)
            .where(Memory.run_id == run_id, Memory.source == "conversation")
        )
    return {
        "memoryTotal": int(total or 0),
        "memoryEmbedded": int(embedded or 0),
        "conversationMemories": int(conversation or 0),
    }


async def _main() -> int:
    args = _args()
    if not args.live:
        print(json.dumps({"live": False, "requestSent": False, "status": "dry_run"}))
        return 0
    if args.max_model_calls <= 0:
        raise SystemExit("--max-model-calls must be positive")
    if args.step_timeout_seconds <= 0 or args.run_timeout_seconds <= 0:
        raise SystemExit("acceptance timeouts must be positive")
    required = ("ARK_API_KEY", "ARK_MODEL", "ARK_EMBEDDING_MODEL", "DATABASE_URL")
    if any(not os.environ.get(name, "").strip() for name in required):
        raise SystemExit("real chat acceptance is not fully configured")

    registry = ScenarioLoader(_ROOT / "core" / "scenario").load()
    database_url = os.environ["DATABASE_URL"].strip()
    text_client = ArkClient()
    embedding_base = os.environ.get("ARK_EMBEDDING_BASE_URL", "").strip()
    embedding_client = ArkEmbeddingClient(
        ArkEmbeddingSettings(
            model=os.environ["ARK_EMBEDDING_MODEL"].strip(),
            api_key=os.environ["ARK_API_KEY"].strip(),
            base_url=embedding_base or DEFAULT_ARK_EMBEDDING_BASE_URL,
        )
    )
    base_repository = SQLAlchemyRunRepository(
        database_url, chapter_id=registry.chapter_id
    )
    report: dict[str, Any] = {
        "live": True,
        "requestSent": False,
        "seed": args.seed,
        "status": "failed",
        "errorCode": None,
    }
    run_id: str | None = None
    stage = "preflight"
    temporary_run_deleted = False
    started = time.perf_counter()
    try:
        if not await base_repository.healthcheck():
            raise RuntimeError("database_healthcheck_failed")
        await sync_scenario(base_repository.session_factory, registry)
        # Public fixed text proves model/dimension before any private payload.
        await embedding_client.embed("青槐老巷真实聊天闭环向量预检")
        report["requestSent"] = True
        report["embeddingPreflight"] = embedding_client.last_metadata

        indexer = MemoryEmbeddingIndexer(
            base_repository.session_factory, embedding_client, batch_size=8
        )
        repository = IndexingRunRepository(base_repository, indexer)
        retriever = DatabaseMemoryRetriever(
            base_repository.session_factory, embedding_port=embedding_client
        )
        bounded_model = _BoundedTextModel(text_client, args.max_model_calls)
        service = RunService(
            registry,
            repository=repository,
            text_model=bounded_model,
            memory_retriever=retriever,
            seed=args.seed,
        )
        engine = WorldEngine(service)
        stage = "create_run"
        created = await service.create_run(seed=args.seed)
        run_id = str(created["runId"])
        report["runId"] = run_id
        deadline = time.perf_counter() + args.run_timeout_seconds
        joined_conversation_id: str | None = None
        attempted_conversations: set[str] = set()

        while True:
            run = await service.get_run_entity(run_id)
            if run.clock.as_dict()["label"] == "Day1 18:00":
                break
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise TimeoutError("real chat acceptance timed out")
            clock_minutes = run.clock.current.clock_minutes
            step_minutes = (
                60
                if joined_conversation_id is not None
                else 1 if clock_minutes % 60 == 0 else 60 - clock_minutes % 60
            )
            stage = f"world_step:{run.clock.as_dict()['label']}"
            await asyncio.wait_for(
                engine.step(
                    run_id,
                    int(step_minutes * registry.real_seconds_per_virtual_minute),
                    command_id=(
                        f"real_chat_step_{run.clock.current.day}_"
                        f"{run.clock.current.clock_minutes}_{step_minutes}"
                    ),
                ),
                timeout=min(args.step_timeout_seconds, remaining),
            )
            run = await service.get_run_entity(run_id)
            if joined_conversation_id is not None:
                continue
            candidates = [
                conversation
                for conversation in run.open_conversations()
                if registry.player_actor_id not in conversation.participants
                and len(conversation.participants) < 3
                and conversation.conversation_id not in attempted_conversations
            ]
            for conversation in candidates:
                attempted_conversations.add(conversation.conversation_id)
                stage = "player_join"
                joined = await asyncio.wait_for(
                    service.player_join(
                        run_id,
                        conversation.conversation_id,
                        command_id=f"real_chat_join_{conversation.conversation_id}",
                    ),
                    timeout=min(args.step_timeout_seconds, remaining),
                )
                if joined.get("joinRequest", {}).get("status") != "accepted":
                    continue
                joined_conversation_id = conversation.conversation_id
                stage = "player_message"
                await asyncio.wait_for(
                    service.player_message(
                        run_id,
                        joined_conversation_id,
                        "我想听听你们准备怎样合作保住书店，也愿意帮忙协调分歧。",
                        command_id="real_chat_player_message",
                    ),
                    timeout=min(args.step_timeout_seconds, remaining),
                )
                break

        stage = "collect_metrics"
        run = await service.get_run_entity(run_id)
        event_counts: dict[str, int] = {}
        for event in run.events:
            event_counts[event.event_type] = event_counts.get(event.event_type, 0) + 1
        npc_invitations = sum(
            1
            for event in run.events
            if event.event_type == "invitation_requested"
            and str(event.payload.get("initiatorActorId", "")).startswith("npc_")
        )
        npc_messages = sum(
            1
            for messages in run.messages.values()
            for message in messages
            if str(message.get("authorActorId", "")).startswith("npc_")
        )
        counts = await _database_counts(base_repository, run_id)
        provider_metrics = text_client.metrics_snapshot()
        report.update(
            {
                "runId": run_id,
                "finalWorldTime": run.clock.as_dict()["label"],
                "npcInvitations": npc_invitations,
                "playerJoined": joined_conversation_id is not None,
                "npcMessages": npc_messages,
                "modelLogicalCalls": bounded_model.calls,
                "modelPhysicalRequests": provider_metrics["providerAttempts"],
                "providerRetries": provider_metrics["providerRetries"],
                "events": event_counts,
                "database": counts,
            }
        )
        await repository.close()

        recovered_repository = SQLAlchemyRunRepository(
            database_url, chapter_id=registry.chapter_id
        )
        recovered = await recovered_repository.get(run_id)
        recovered_counts = await _database_counts(recovered_repository, run_id)
        report["repositoryRecovered"] = recovered is not None
        report["databaseAfterRestart"] = recovered_counts
        gates = {
            "npcDailyAction": event_counts.get("npc_thought_started", 0) > 0,
            "npcInvitation": npc_invitations > 0,
            "conversationCreated": event_counts.get("conversation_created", 0) > 0,
            "playerJoinedAndMessaged": joined_conversation_id is not None,
            "npcSpoke": npc_messages > 0,
            "npcConsolidated": event_counts.get("npc_consolidated", 0) > 0,
            "conversationMemoryStored": counts["conversationMemories"] > 0,
            "allMemoriesEmbedded": counts["memoryEmbedded"] == counts["memoryTotal"],
            "repositoryRecovered": recovered is not None,
            "embeddingSurvivedRestart": recovered_counts["memoryEmbedded"]
            == counts["memoryEmbedded"],
        }
        report["gates"] = gates
        report["status"] = "passed" if all(gates.values()) else "failed"
        await recovered_repository.close()
    except Exception as exc:
        report["errorCode"] = type(exc).__name__
        report["errorStage"] = stage
        report["modelLogicalCalls"] = (
            bounded_model.calls if "bounded_model" in locals() else 0
        )
        report["modelPhysicalRequests"] = text_client.metrics_snapshot()[
            "providerAttempts"
        ]
    finally:
        if run_id is not None and not args.keep_run:
            try:
                async with base_repository.session_factory() as session:
                    async with session.begin():
                        deleted = await session.execute(
                            delete(ChapterRun).where(ChapterRun.run_id == run_id)
                        )
                temporary_run_deleted = bool(deleted.rowcount)
            except Exception:
                report["cleanupError"] = True
        report["temporaryRunDeleted"] = temporary_run_deleted
        if run_id is not None and not args.keep_run and not temporary_run_deleted:
            report["status"] = "failed"
        report["latencyMs"] = int((time.perf_counter() - started) * 1000)
        await embedding_client.close()
        await text_client.close()
        try:
            await base_repository.close()
        except Exception:
            pass

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Safe report: {args.output}")
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
