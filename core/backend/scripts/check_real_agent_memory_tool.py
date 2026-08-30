"""Probe one real NPC Agent memory-tool round trip without exposing content.

The command is a dry run unless ``--live`` is supplied.  Live mode creates one
temporary PostgreSQL Run, asks one NPC a bounded question about prior events,
and writes only counters and gate results.  The temporary Run is deleted in a
``finally`` block unless ``--keep-run`` is explicitly requested.
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
from core.backend.app.db.models import ChapterRun  # noqa: E402
from core.backend.app.orchestration.run_service import RunService  # noqa: E402
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
from sqlalchemy import delete  # noqa: E402


class _BoundedTextModel:
    configured = True

    def __init__(self, delegate: ArkClient, maximum: int) -> None:
        self.delegate = delegate
        self.maximum = maximum
        self.calls = 0

    async def generate(self, request: Any) -> Any:
        if self.calls >= self.maximum:
            raise RuntimeError("memory-tool probe model-call budget exhausted")
        self.calls += 1
        return await self.delegate.generate(request)


class _CountingRetriever:
    """Observe owner-safe retrieval without logging its query or results."""

    def __init__(self, delegate: DatabaseMemoryRetriever) -> None:
        self.delegate = delegate
        self.calls = 0
        self.recalled_ids = 0
        self.vector_hits = 0
        self.graph_hits = 0

    async def search(self, **kwargs: Any) -> Any:
        self.calls += 1
        result = await self.delegate.search(**kwargs)
        self.recalled_ids += len(result.memory_ids)
        self.vector_hits += result.vector_hits
        self.graph_hits += result.graph_hits
        return result


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--seed", type=int, default=20260821)
    parser.add_argument(
        "--npc-id",
        default=None,
        help="optional NPC actor ID; defaults to the first available NPC",
    )
    parser.add_argument("--max-model-calls", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--keep-run", action="store_true")
    parser.add_argument(
        "--require-chapter-effect",
        action="store_true",
        help="also require a spoken, evidence-bound chapter stance change",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("simulation_reports/real_agent_memory_tool.json"),
    )
    return parser.parse_args()


async def _main() -> int:
    args = _args()
    if not args.live:
        print(json.dumps({"live": False, "requestSent": False, "status": "dry_run"}))
        return 0
    if args.max_model_calls <= 0 or args.timeout_seconds <= 0:
        raise SystemExit("probe budgets must be positive")
    required = ("ARK_API_KEY", "ARK_MODEL", "ARK_EMBEDDING_MODEL", "DATABASE_URL")
    if any(not os.environ.get(name, "").strip() for name in required):
        raise SystemExit("real Agent memory-tool probe is not fully configured")

    registry = ScenarioLoader(_ROOT / "core" / "scenario").load()
    database_url = os.environ["DATABASE_URL"].strip()
    text_client = ArkClient()
    embedding_client = ArkEmbeddingClient(
        ArkEmbeddingSettings(
            model=os.environ["ARK_EMBEDDING_MODEL"].strip(),
            api_key=os.environ["ARK_API_KEY"].strip(),
            base_url=(
                os.environ.get("ARK_EMBEDDING_BASE_URL", "").strip()
                or DEFAULT_ARK_EMBEDDING_BASE_URL
            ),
        )
    )
    base_repository = SQLAlchemyRunRepository(
        database_url, chapter_id=registry.chapter_id
    )
    report: dict[str, Any] = {
        "live": True,
        "requestSent": False,
        "status": "failed",
        "errorCode": None,
    }
    run_id: str | None = None
    temporary_run_deleted = False
    started = time.perf_counter()
    bounded_model: _BoundedTextModel | None = None
    counting_retriever: _CountingRetriever | None = None
    try:
        if not await base_repository.healthcheck():
            raise RuntimeError("database_healthcheck_failed")
        await sync_scenario(base_repository.session_factory, registry)
        await embedding_client.embed("青槐老巷 Agent 记忆工具向量预检")
        report["requestSent"] = True

        repository = IndexingRunRepository(
            base_repository,
            MemoryEmbeddingIndexer(
                base_repository.session_factory,
                embedding_client,
                batch_size=8,
            ),
        )
        counting_retriever = _CountingRetriever(
            DatabaseMemoryRetriever(
                base_repository.session_factory,
                embedding_port=embedding_client,
            )
        )
        bounded_model = _BoundedTextModel(text_client, args.max_model_calls)
        service = RunService(
            registry,
            repository=repository,
            text_model=bounded_model,
            memory_retriever=counting_retriever,
            seed=args.seed,
        )
        created = await service.create_run(seed=args.seed)
        run_id = str(created["runId"])
        run = await service.get_run_entity(run_id)
        pending_invitation_actors = {
            str(actor_id)
            for invitation in run.invitations.values()
            if invitation.get("status") == "pending"
            for actor_id in (
                invitation.get("initiatorActorId"),
                invitation.get("targetActorId"),
            )
            if actor_id is not None
        }
        available_npcs = [
            npc.actor_id
            for npc in registry.npcs
            if run.actor_open_conversation(npc.actor_id) is None
            and npc.actor_id not in pending_invitation_actors
        ]
        if not available_npcs:
            raise RuntimeError("no_available_npc_for_probe")
        if args.npc_id is not None and args.npc_id not in available_npcs:
            raise RuntimeError("requested_npc_not_available_for_probe")
        probe_npc_id = args.npc_id or available_npcs[0]
        conversation_result = await service.create_conversation(
            run_id,
            [probe_npc_id, registry.player_actor_id],
            command_id="real_memory_probe_conversation",
        )
        conversation_id = str(
            conversation_result["conversation"]["conversationId"]
        )
        player_text = (
            "我支持你的公开主张。结合你过去和书店的经历，你现在是否支持把自己的主张纳入最终方案？请明确说明立场。"
            if args.require_chapter_effect
            else "你以前和周老板或者他的家人是否发生过什么旧事？这会影响现在的文社吗？"
        )
        message_result = await asyncio.wait_for(
            service.player_message(
                run_id,
                conversation_id,
                player_text,
                command_id="real_memory_probe_message",
            ),
            timeout=args.timeout_seconds,
        )
        await service.wait_for_chat_idle(
            run_id,
            conversation_id,
            timeout=args.timeout_seconds,
        )
        message_result = await service.get_messages(
            run_id,
            conversation_id,
        )
        traces = tuple(service.agent_runtime.trace_sink.snapshot())
        tool_traces = tuple(trace for trace in traces if trace.tool_used)
        post_recall_tool_traces = tuple(
            trace
            for trace in tool_traces
            if "chat_after_recall" in trace.node_path
        )
        npc_messages = sum(
            1
            for message in message_result.get("messages", [])
            if str(message.get("authorActorId", "")).startswith("npc_")
        )
        if args.require_chapter_effect:
            current_run = await service.get_run_entity(run_id)
            current_conversation = current_run.conversations[conversation_id]
            if (
                current_conversation.is_open
                and probe_npc_id in current_conversation.participants
            ):
                await asyncio.wait_for(
                    service.remove_participant(
                        run_id,
                        conversation_id,
                        probe_npc_id,
                        command_id="real_memory_probe_leave",
                    ),
                    timeout=args.timeout_seconds,
                )
            current_run = await service.get_run_entity(run_id)
            owned_agenda_ids = {
                agenda.agenda_id
                for agenda in registry.public_agendas
                if agenda.owner_npc_id == probe_npc_id
            }
            chapter_effect_committed = (
                current_run.chapter_actor_stances.get(probe_npc_id, "unknown")
                != "unknown"
                or any(
                    current_run.chapter_agenda_stances.get(
                        (agenda_id, probe_npc_id), "unknown"
                    )
                    != "unknown"
                    for agenda_id in owned_agenda_ids
                )
                or (
                    probe_npc_id == "npc_005"
                    and current_run.zhou_authorization != "none"
                )
            )
        else:
            chapter_effect_committed = False
        provider_metrics = text_client.metrics_snapshot()
        if args.require_chapter_effect:
            gates = {
                "npcSpoke": npc_messages > 0,
                "chapterEffectCommitted": chapter_effect_committed,
            }
        else:
            gates = {
                "memoryToolCalled": counting_retriever.calls > 0,
                "memoryReturned": counting_retriever.recalled_ids > 0,
                "vectorHit": counting_retriever.vector_hits > 0,
                "agentTraceRecordedTool": len(tool_traces) > 0,
                "agentReachedPostRecallDecision": len(post_recall_tool_traces) > 0,
            }
        report.update(
            {
                "modelLogicalCalls": bounded_model.calls,
                "modelPhysicalRequests": provider_metrics["providerAttempts"],
                "providerRetries": provider_metrics["providerRetries"],
                "memoryRetrievalCalls": counting_retriever.calls,
                "memoryReturnedIds": counting_retriever.recalled_ids,
                "memoryVectorHits": counting_retriever.vector_hits,
                "memoryGraphHits": counting_retriever.graph_hits,
                "agentToolTraces": len(tool_traces),
                "agentPostRecallToolTraces": len(post_recall_tool_traces),
                "toolTraceNodePaths": [list(trace.node_path) for trace in tool_traces],
                "toolTraceFailureCodes": [trace.failure_code for trace in tool_traces],
                "npcMessages": npc_messages,
                "chapterEffectCommitted": chapter_effect_committed,
                "gates": gates,
                "status": "passed" if all(gates.values()) else "failed",
            }
        )
    except Exception as exc:
        report["errorCode"] = type(exc).__name__
        report["modelLogicalCalls"] = bounded_model.calls if bounded_model else 0
        report["memoryRetrievalCalls"] = (
            counting_retriever.calls if counting_retriever else 0
        )
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
