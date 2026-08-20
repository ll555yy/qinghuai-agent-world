"""Verify persisted Ark vectors and owner-safe semantic recall without printing Memory text.

Dry-run by default. The live check embeds one fixed semantic query, reopens the
repository, and reports only identifiers, counts, and provider metadata.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from app.ai.ark_embedding import (
    DEFAULT_ARK_EMBEDDING_BASE_URL,
    ArkEmbeddingClient,
    ArkEmbeddingSettings,
)
from app.ai.protocols import MemoryQuery
from app.db.models import Memory
from app.persistence.memory_retriever import DatabaseMemoryRetriever
from app.persistence.sqlalchemy_repository import SQLAlchemyRunRepository
from app.settings import Settings
from sqlalchemy import select

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


OWNER_ID = "npc_001"
EXPECTED_MEMORY_ID = "memory_seed_rel_npc_001_npc_005"
SEMANTIC_QUERY = "那位沉默寡言的守店人，是否愿意让大家一起守住老街的文化空间"


async def _run(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    report: dict[str, Any] = {
        "live": args.live,
        "runId": args.run_id,
        "ownerNpcId": OWNER_ID,
        "expectedMemoryId": EXPECTED_MEMORY_ID,
        "requestSent": False,
    }
    if not args.live:
        report.update(status="dry_run", success=True)
        return report, 0

    settings = Settings.from_environment()
    if not settings.database_url or not settings.embedding_model:
        report.update(status="configuration_error", success=False)
        return report, 2

    provider = ArkEmbeddingClient(
        ArkEmbeddingSettings(
            model=settings.embedding_model,
            base_url=settings.embedding_base_url or DEFAULT_ARK_EMBEDDING_BASE_URL,
        )
    )
    if not provider.configured:
        report.update(status="configuration_error", success=False)
        return report, 2

    # Open and close once before retrieval so the check proves persisted state,
    # not an in-process cache created during backfill.
    first_repository = SQLAlchemyRunRepository(settings.database_url)
    await first_repository.close()
    repository = SQLAlchemyRunRepository(settings.database_url)
    try:
        retriever = DatabaseMemoryRetriever(
            repository.session_factory,
            embedding_port=provider,
        )
        report["requestSent"] = True
        result = await retriever.search(
            run_id=args.run_id,
            owner_npc_id=OWNER_ID,
            query=MemoryQuery(queryText=SEMANTIC_QUERY, limit=8),
        )
        selected_ids = list(result.memory_ids)
        async with repository.session_factory() as session:
            owner_rows = (
                await session.execute(
                    select(Memory.memory_id, Memory.owner_npc_id).where(
                        Memory.run_id == args.run_id,
                        Memory.memory_id.in_(selected_ids),
                    )
                )
            ).all()
        owner_violation = any(owner_id != OWNER_ID for _, owner_id in owner_rows)
        target_recalled = EXPECTED_MEMORY_ID in selected_ids
        success = bool(selected_ids) and target_recalled and not owner_violation
        report.update(
            status="completed" if success else "acceptance_failed",
            success=success,
            selectedMemoryIds=selected_ids,
            selectedCount=len(selected_ids),
            vectorHits=result.vector_hits,
            graphHits=result.graph_hits,
            targetRecalled=target_recalled,
            ownerViolation=owner_violation,
            embedding=provider.last_metadata,
            repositoryReopened=True,
        )
        return report, 0 if success else 1
    except Exception as exc:
        report.update(
            status="runtime_error",
            success=False,
            errorType=type(exc).__name__,
        )
        return report, 1
    finally:
        await provider.close()
        await repository.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    report, code = asyncio.run(_run(args))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
