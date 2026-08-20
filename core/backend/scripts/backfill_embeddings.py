"""Backfill NULL Memory embeddings using the explicitly configured Ark model.

Run from ``core/backend`` after applying Alembic migrations.  The command is
idempotent by default and never prints credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.ai.ark_embedding import (
    DEFAULT_ARK_EMBEDDING_BASE_URL,
    ArkEmbeddingClient,
    ArkEmbeddingSettings,
)
from app.persistence.embedding_indexer import MemoryEmbeddingIndexer
from app.persistence.sqlalchemy_repository import SQLAlchemyRunRepository
from app.settings import Settings

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def _run(args: argparse.Namespace) -> None:
    if not args.live:
        raise SystemExit("dry-run: add --live to call the configured Embedding model")
    settings = Settings.from_environment()
    if not settings.embedding_model:
        raise SystemExit("ARK_EMBEDDING_MODEL must be explicitly configured")
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is required")
    provider_settings = ArkEmbeddingSettings(
        model=settings.embedding_model,
        base_url=settings.embedding_base_url or DEFAULT_ARK_EMBEDDING_BASE_URL,
    )
    provider = ArkEmbeddingClient(provider_settings)
    if not provider.configured:
        raise SystemExit("ARK_API_KEY is not configured")
    repository = SQLAlchemyRunRepository(settings.database_url)
    try:
        indexer = MemoryEmbeddingIndexer(
            repository.session_factory,
            provider,
            batch_size=args.batch_size,
            dimensions=settings.memory_embedding_dimensions,
        )
        result = await indexer.index_missing(
            run_id=args.run_id,
            limit=args.limit,
            force=args.force,
        )
        print(
            f"indexed={result.indexed} skipped={result.skipped} failed={result.failed}"
        )
    finally:
        await provider.close()
        await repository.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument(
        "--batch-size",
        type=int,
        default=8,
        help="provider batch size; 8 is the verified Ark-safe project default",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-embed rows that already have a vector",
    )
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error("--limit must be between 1 and 1000")
    if not 1 <= args.batch_size <= 256:
        parser.error("--batch-size must be between 1 and 256")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
