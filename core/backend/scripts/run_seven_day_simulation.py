"""Opt-in CLI for one bounded seven-day simulation.

Examples (run from the repository root)::

    python core/backend/scripts/run_seven_day_simulation.py --route observer
    python core/backend/scripts/run_seven_day_simulation.py --real --route pro_lin

The second form is the only form that may call Ark, and it still refuses
before creating a Run when ``ARK_API_KEY`` is absent.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, cast

from dotenv import load_dotenv

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Direct script execution puts ``core/backend/scripts`` on sys.path rather
# than the repository root.  Add only this known workspace root so the CLI is
# also usable without installing the package.
_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_ROOT / ".env", override=False)
sys.path.insert(0, str(_ROOT))

from core.backend.app.scenario.loader import ScenarioLoader  # noqa: E402
from core.backend.app.simulation.runner import (  # noqa: E402
    DEFAULT_EMBEDDING_CNY_PER_MILLION,
    DEFAULT_SIMULATION_SEED,
    DEFAULT_TEXT_INPUT_CNY_PER_MILLION,
    DEFAULT_TEXT_OUTPUT_CNY_PER_MILLION,
    ROUTE_AGENDAS,
    SevenDaySimulationRunner,
    SimulationRoute,
    real_quality_gate_failures,
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--route",
        choices=("all", "observer", "pro_lin", "pro_zhao"),
        default="all",
        help="all runs observer, pro_lin and pro_zhao once per --runs cycle",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SIMULATION_SEED)
    parser.add_argument(
        "--selected-agenda-id",
        choices=tuple(value for value in ROUTE_AGENDAS.values() if value is not None),
        default=None,
        help="select the matching support route explicitly; mutually exclusive with --route all",
    )
    parser.add_argument("--real", action="store_true", help="explicitly allow configured Ark calls")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--max-calls-per-run", type=int, default=600)
    parser.add_argument("--max-total-calls", type=int, default=1800)
    parser.add_argument("--step-timeout-seconds", type=float, default=900.0)
    parser.add_argument("--run-timeout-seconds", type=float, default=5400.0)
    parser.add_argument(
        "--keep-runs",
        action="store_true",
        help="reuse one service/repository for the batch; Postgres rows remain durable either way",
    )
    parser.add_argument("--backend", choices=("memory", "postgres"), default="memory")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--output", type=Path, default=Path("simulation_reports"))
    parser.add_argument(
        "--text-input-cny-per-million",
        type=float,
        default=DEFAULT_TEXT_INPUT_CNY_PER_MILLION,
    )
    parser.add_argument(
        "--text-output-cny-per-million",
        type=float,
        default=DEFAULT_TEXT_OUTPUT_CNY_PER_MILLION,
    )
    parser.add_argument(
        "--embedding-cny-per-million",
        type=float,
        default=DEFAULT_EMBEDDING_CNY_PER_MILLION,
    )
    return parser.parse_args()


async def _main() -> int:
    args = _args()
    if args.runs <= 0:
        raise SystemExit("--runs must be positive")
    if args.max_calls_per_run is not None and args.max_calls_per_run <= 0:
        raise SystemExit("--max-calls-per-run must be positive")
    if args.max_total_calls is not None and args.max_total_calls <= 0:
        raise SystemExit("--max-total-calls must be positive")
    if args.step_timeout_seconds <= 0 or args.run_timeout_seconds <= 0:
        raise SystemExit("simulation timeouts must be positive")
    if min(
        args.text_input_cny_per_million,
        args.text_output_cny_per_million,
        args.embedding_cny_per_million,
    ) < 0:
        raise SystemExit("model pricing rates cannot be negative")
    if args.real and args.backend != "postgres":
        raise SystemExit("real seven-day simulations require --backend postgres")
    embedding_model = os.environ.get("ARK_EMBEDDING_MODEL", "").strip()
    if args.real and not embedding_model:
        raise SystemExit("ARK_EMBEDDING_MODEL is required for real seven-day simulations")
    if args.selected_agenda_id is not None:
        if args.route == "all":
            raise SystemExit("--selected-agenda-id cannot be combined with --route all")
        expected_route = next(
            route
            for route, agenda_id in ROUTE_AGENDAS.items()
            if agenda_id == args.selected_agenda_id
        )
        if args.route != expected_route:
            raise SystemExit("--selected-agenda-id does not match --route")
    registry = ScenarioLoader(_ROOT / "core" / "scenario").load()
    client = None
    if args.real and os.environ.get("ARK_API_KEY", "").strip():
        from core.backend.app.ai.ark_client import ArkClient

        client = ArkClient()
    service = None
    repository: Any = None
    base_repository = None
    database_url = args.database_url or os.environ.get("DATABASE_URL")
    retriever = None
    embedding_client = None
    embedding_preflight: dict[str, object] | None = None
    if (
        args.backend == "postgres"
        and (not args.real or client is not None)
        and not database_url
    ):
        raise SystemExit("DATABASE_URL is required for the PostgreSQL simulation backend")
    if (
        args.backend == "postgres"
        and database_url
        and (not args.real or bool(os.environ.get("ARK_API_KEY", "").strip()))
    ):
        from core.backend.app.db.bootstrap import sync_scenario
        from core.backend.app.orchestration.run_service import RunService
        from core.backend.app.persistence.embedding_indexer import MemoryEmbeddingIndexer
        from core.backend.app.persistence.indexing_repository import IndexingRunRepository
        from core.backend.app.persistence.memory_retriever import DatabaseMemoryRetriever
        from core.backend.app.persistence.sqlalchemy_repository import SQLAlchemyRunRepository

        base_repository = SQLAlchemyRunRepository(
            database_url, chapter_id=registry.chapter_id
        )
        if not await base_repository.healthcheck():
            raise SystemExit("Postgres healthcheck failed; apply migrations first")
        await sync_scenario(base_repository.session_factory, registry)
        if args.real and embedding_model and client is not None:
            from core.backend.app.ai.ark_embedding import (
                DEFAULT_ARK_EMBEDDING_BASE_URL,
                ArkEmbeddingClient,
                ArkEmbeddingSettings,
            )

            embedding_base_url = os.environ.get("ARK_EMBEDDING_BASE_URL", "").strip()
            embedding_client = ArkEmbeddingClient(
                ArkEmbeddingSettings(
                    model=embedding_model,
                    api_key=os.environ.get("ARK_API_KEY", "").strip(),
                    base_url=embedding_base_url or DEFAULT_ARK_EMBEDDING_BASE_URL,
                )
            )
            try:
                await embedding_client.embed("青槐老巷七日模拟向量预检")
            except Exception as exc:
                raise SystemExit(
                    f"embedding preflight failed safely: {type(exc).__name__}"
                ) from None
            embedding_preflight = embedding_client.last_metadata
        retriever = DatabaseMemoryRetriever(
            base_repository.session_factory,
            embedding_port=embedding_client,
        )
        repository = base_repository
        if embedding_client is not None:
            embedding_indexer = MemoryEmbeddingIndexer(
                base_repository.session_factory,
                embedding_client,
                batch_size=8,
            )
            repository = IndexingRunRepository(base_repository, embedding_indexer)
        service = RunService(
            registry,
            repository=repository,
            text_model=client,
            memory_retriever=retriever,
            seed=args.seed,
        )
    if args.keep_runs and service is None:
        from core.backend.app.orchestration.run_service import RunService

        service = RunService(registry, text_model=client, seed=args.seed)

    reports = []
    total_calls = 0
    route_cycle = (
        ("observer", "pro_lin", "pro_zhao")
        if args.route == "all"
        else (args.route,)
    )
    requested_run_count = args.runs * len(route_cycle)
    for index in range(requested_run_count):
        route = cast(SimulationRoute, route_cycle[index % len(route_cycle)])
        remaining = None
        if args.max_total_calls is not None:
            remaining = args.max_total_calls - total_calls
            if remaining <= 0:
                break
        per_run_limit = args.max_calls_per_run
        if remaining is not None:
            per_run_limit = (
                remaining
                if per_run_limit is None
                else min(per_run_limit, remaining)
            )
        run_service = service
        if not args.keep_runs and args.backend == "memory":
            run_service = None
        embedding_before = (
            embedding_client.metrics_snapshot()
            if embedding_client is not None
            else {"completedRequests": 0, "totalTokens": 0}
        )
        report = await SevenDaySimulationRunner(
            registry,
            seed=args.seed + index,
            max_calls_per_run=per_run_limit,
            step_timeout_seconds=args.step_timeout_seconds,
            run_timeout_seconds=args.run_timeout_seconds,
        ).run(
            route=route,
            mode="real" if args.real else "offline",
            text_model=client,
            service=run_service,
            memory_retriever=retriever,
            allow_network=args.real and args.backend == "postgres",
        )
        embedding_after = (
            embedding_client.metrics_snapshot()
            if embedding_client is not None
            else embedding_before
        )
        report.metrics.embedding_provider_requests = (
            embedding_after["completedRequests"]
            - embedding_before["completedRequests"]
        )
        report.metrics.embedding_tokens = (
            embedding_after["totalTokens"] - embedding_before["totalTokens"]
        )
        report.metrics.text_input_cny_per_million = args.text_input_cny_per_million
        report.metrics.text_output_cny_per_million = args.text_output_cny_per_million
        report.metrics.embedding_cny_per_million = args.embedding_cny_per_million
        reports.append(report)
        total_calls += report.metrics.physical_provider_requests or sum(
            report.metrics.provider_calls.values()
        )

    if embedding_client is not None:
        await embedding_client.close()
    if repository is not None:
        await repository.close()
        repository = None
    if base_repository is not None and database_url:
        from core.backend.app.db.models import ChapterRun
        from core.backend.app.persistence.sqlalchemy_repository import (
            SQLAlchemyRunRepository,
        )
        from sqlalchemy import delete

        recovered_repository = SQLAlchemyRunRepository(
            database_url, chapter_id=registry.chapter_id
        )
        run_ids = [report.run_id for report in reports if report.run_id]
        for report in reports:
            if report.run_id:
                report.metrics.repository_recovered = (
                    await recovered_repository.get(report.run_id)
                ) is not None
        if run_ids and not args.keep_runs:
            async with recovered_repository.session_factory() as session:
                async with session.begin():
                    await session.execute(
                        delete(ChapterRun).where(ChapterRun.run_id.in_(run_ids))
                    )
            # ``get`` intentionally keeps stable Run identity objects in a
            # repository-local cache.  Verify deletion through a fresh
            # repository instead of mistaking that cache for a surviving row.
            await recovered_repository.close()
            verification_repository = SQLAlchemyRunRepository(
                database_url, chapter_id=registry.chapter_id
            )
            for report in reports:
                if report.run_id:
                    report.metrics.temporary_run_deleted = (
                        await verification_repository.get(report.run_id)
                    ) is None
            await verification_repository.close()
        else:
            await recovered_repository.close()

    if args.real:
        for report in reports:
            report.metrics.quality_gate_failures = real_quality_gate_failures(report)

    payload = {
        "route": args.route,
        "mode": "real" if args.real else "offline",
        "seed": args.seed,
        "runsRequested": requested_run_count,
        "runsCompleted": len(reports),
        "keepRuns": args.keep_runs,
        "backend": args.backend,
        "maxCallsPerRun": args.max_calls_per_run,
        "maxTotalCalls": args.max_total_calls,
        "stepTimeoutSeconds": args.step_timeout_seconds,
        "runTimeoutSeconds": args.run_timeout_seconds,
        "totalProviderCalls": total_calls,
        "pricingCnyPerMillionTokens": {
            "textInput": args.text_input_cny_per_million,
            "textOutput": args.text_output_cny_per_million,
            "embedding": args.embedding_cny_per_million,
        },
        "embeddingPreflight": embedding_preflight,
        "reports": [report.to_dict() for report in reports],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    json_path = args.output / "seven_day_simulation_batch.json"
    markdown_path = args.output / "seven_day_simulation_batch.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_lines = [
        "# Qinghuai seven-day simulation batch",
        "",
        f"- Route: `{args.route}`",
        f"- Mode: `{'real' if args.real else 'offline'}`",
        f"- Runs: `{len(reports)}/{requested_run_count}`",
        f"- Backend: `{args.backend}`",
        f"- Provider calls: `{total_calls}`",
        f"- Temporary Runs deleted: `{all(report.metrics.temporary_run_deleted is True for report in reports if report.run_id) if not args.keep_runs else 'kept by request'}`",
        "",
        "| Run | Seed | Route | Player lines | Changed NPC stances | Goal completion | Day7 branch | Player result | Cost CNY | Recall | Vector/Graph | Recovered | Deleted | Gates |",
        "|---:|---:|---|---:|---:|---:|---|---|---:|---:|---:|---|---|---|",
    ]
    for index, report in enumerate(reports, start=1):
        metrics = report.metrics.to_dict()
        markdown_lines.append(
            f"| {index} | {report.seed} | `{report.route}` "
            f"| {metrics['speech']['player']} "
            f"| {metrics['chapterStanceChangeCount']} "
            f"| {metrics['goalCompletionRate'] if metrics['goalCompletionRate'] is not None else 'n/a'} "
            f"| `{metrics['day7Branch'] or 'n/a'}` "
            f"| `{metrics['playerResult'] or 'n/a'}` "
            f"| {metrics['costEstimate']['totalCny']} "
            f"| {metrics['memoryRetrieval']['calls']} "
            f"| {metrics['memoryRetrieval']['vectorHits']}/{metrics['memoryRetrieval']['graphHits']} "
            f"| `{metrics['repositoryRecovered']}` "
            f"| `{metrics['temporaryRunDeleted']}` "
            f"| `{','.join(metrics['qualityGateFailures']) or 'none'}` |"
        )
    markdown_path.write_text("\n".join(markdown_lines) + "\n", encoding="utf-8")
    if client is not None:
        await client.close()
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")
    if reports and any(
        report.metrics.rejected
        or report.metrics.abnormal_termination is not None
        or report.metrics.quality_gate_failures
        for report in reports
    ):
        print("Simulation batch did not pass its acceptance gates; inspect the safe report.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
