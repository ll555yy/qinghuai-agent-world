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
import re
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
from core.backend.app.simulation.manifest import (  # noqa: E402
    DEFAULT_MANIFEST_PATH,
    AttemptLedger,
    load_manifest,
    planned_attempts,
)
from core.backend.app.simulation.runner import (  # noqa: E402
    DEFAULT_EMBEDDING_CNY_PER_MILLION,
    DEFAULT_SIMULATION_SEED,
    DEFAULT_TEXT_INPUT_CNY_PER_MILLION,
    DEFAULT_TEXT_OUTPUT_CNY_PER_MILLION,
    ROUTE_AGENDAS,
    SevenDaySimulationRunner,
    SimulationReport,
    SimulationRoute,
    real_quality_gate_failures,
)


def _atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def _write_attempt_checkpoint(report: SimulationReport, output: Path) -> tuple[Path, Path]:
    """Persist one completed attempt before the batch advances.

    Batch-level cleanup may enrich the final report later, but this checkpoint
    keeps provider usage, gameplay metrics, and the terminal attempt identity
    recoverable if a later provider outage interrupts the process.
    """

    identity = report.attempt_id or f"{report.route}-{report.seed}"
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", identity).strip("_")
    directory = output / "attempt_reports"
    json_path = directory / f"{stem}.json"
    markdown_path = directory / f"{stem}.md"
    _atomic_write_text(json_path, report.to_json() + "\n")
    _atomic_write_text(markdown_path, report.to_markdown())
    return json_path, markdown_path


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
        "--manifest",
        type=Path,
        default=None,
        help="versioned preregistration manifest; real runs default to the final v1 matrix",
    )
    parser.add_argument(
        "--attempt-root",
        type=Path,
        default=None,
        help="directory for atomic redacted attempt records (defaults below output)",
    )
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
    manifest: dict[str, Any] | None = None
    manifest_digest: str | None = None
    planned_matrix: tuple[dict[str, Any], ...] = ()
    manifest_path = args.manifest
    if manifest_path is None and args.real:
        manifest_path = DEFAULT_MANIFEST_PATH
    if manifest_path is not None:
        manifest, manifest_digest = load_manifest(manifest_path)
        planned_matrix = planned_attempts(manifest)
        # A preregistered invocation takes its ceilings and prices from the
        # committed manifest.  CLI overrides remain useful for legacy offline
        # runs, but cannot silently change a real preregistration.
        if args.real:
            args.max_calls_per_run = manifest["budget"]["maxCallsPerRun"]
            args.max_total_calls = manifest["budget"]["maxTotalCalls"]
            args.step_timeout_seconds = manifest["budget"]["timeoutsSeconds"]["step"]
            args.run_timeout_seconds = manifest["budget"]["timeoutsSeconds"]["run"]
            args.text_input_cny_per_million = manifest["pricing"][
                "textInputCnyPerMillion"
            ]
            args.text_output_cny_per_million = manifest["pricing"][
                "textOutputCnyPerMillion"
            ]
            args.embedding_cny_per_million = manifest["pricing"][
                "embeddingCnyPerMillion"
            ]
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
    attempt_ledger: AttemptLedger | None = None
    if manifest is not None and manifest_digest is not None:
        attempt_root = args.attempt_root or (args.output / "attempts")
        attempt_ledger = AttemptLedger(
            attempt_root,
            experiment_id=str(manifest["experimentId"]),
            manifest_digest=manifest_digest,
            planned=planned_matrix,
        )
        attempt_ledger.prepare()
    if args.real and args.backend != "postgres":
        raise SystemExit("real seven-day simulations require --backend postgres")
    embedding_model = os.environ.get("ARK_EMBEDDING_MODEL", "").strip()
    if args.real and not embedding_model:
        if attempt_ledger is not None:
            for planned_item in planned_matrix:
                attempt_ledger.finish(
                    planned_item,
                    "not_started",
                    reason="embedding_model_not_configured",
                    infra_valid=False,
                )
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
    if manifest is not None:
        if args.runs != 1:
            raise SystemExit("--runs must remain 1 when a preregistration manifest is used")
        selected_plans = tuple(
            item
            for item in planned_matrix
            if args.route == "all" or item["route"] == args.route
        )
        if not selected_plans:
            raise SystemExit("manifest has no planned attempts for the selected route")
        route_cycle: tuple[str, ...] = tuple(
            dict.fromkeys(str(item["route"]) for item in selected_plans)
        )
        run_plan: tuple[dict[str, Any] | None, ...] = selected_plans
    else:
        route_cycle = (
            ("observer", "pro_lin", "pro_zhao")
            if args.route == "all"
            else (args.route,)
        )
        run_plan = tuple(None for _ in range(args.runs * len(route_cycle)))
    requested_run_count = len(run_plan)
    for index, planned_item in enumerate(run_plan):
        route = cast(
            SimulationRoute,
            planned_item["route"] if planned_item is not None else route_cycle[index % len(route_cycle)],
        )
        seed = (
            int(planned_item["seed"])
            if planned_item is not None
            else args.seed + index
        )
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
            seed=seed,
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
            attempt_ledger=attempt_ledger,
            attempt=planned_item,
            manifest_digest=manifest_digest,
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
        if args.real:
            report.metrics.quality_gate_failures = real_quality_gate_failures(report)
        _write_attempt_checkpoint(report, args.output)
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

    if attempt_ledger is not None:
        # Any manifest row skipped because a batch ceiling was reached still
        # receives a terminal ``not_started`` record.  It remains a planned
        # denominator row and therefore cannot disappear from ITT evidence.
        for planned_item in planned_matrix:
            current = attempt_ledger.get(str(planned_item["attemptId"]))
            if current.get("status") == "not_started" and current.get("terminalAt") is None:
                attempt_ledger.finish(
                    planned_item,
                    "not_started",
                    reason="not_attempted_in_batch",
                    infra_valid=False,
                )
    if args.real:
        for report in reports:
            report.metrics.quality_gate_failures = real_quality_gate_failures(report)
    if attempt_ledger is not None:
        for report in reports:
            if report.attempt_id is None:
                continue
            attempt_ledger.annotate(
                report.attempt_id,
                infra_valid=report.attempt_status == "completed",
                gameplay_pass=not report.metrics.quality_gate_failures,
                run_id=report.run_id,
            )
    for report in reports:
        _write_attempt_checkpoint(report, args.output)

    attempt_records = attempt_ledger.records() if attempt_ledger is not None else []

    payload = {
        "experimentId": manifest["experimentId"] if manifest is not None else None,
        "manifestDigest": manifest_digest,
        "manifestSourceId": (
            f"manifest:{manifest['experimentId']}" if manifest is not None else None
        ),
        "sourceId": f"simulation-batch:{args.route}:{args.seed}",
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
        "plannedRuns": [dict(item) for item in planned_matrix],
        "attempts": attempt_records,
        "reports": [report.to_dict() for report in reports],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    json_path = args.output / "seven_day_simulation_batch.json"
    markdown_path = args.output / "seven_day_simulation_batch.md"
    _atomic_write_text(
        json_path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
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
    _atomic_write_text(markdown_path, "\n".join(markdown_lines) + "\n")
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
